"""Audited demonstration inspection facts, never model-produced or a refund decision."""

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from workbench.domain import audit, fingerprint, get_order
from workbench.models import OrderItem, now
from workbench.return_models import QualityEvent, QualityInspection
from workbench.returns import require_role


def binding(session, actor, body, *, lock=False):
    order = get_order(session, actor, body.order_number, lock=lock)
    query = select(OrderItem).where(
        OrderItem.id == body.item_id,
        OrderItem.order_id == order.id,
        OrderItem.organization_id == actor.organization_id,
    )
    item = session.scalar(query.with_for_update() if lock else query)
    if not item:
        raise HTTPException(404, "Order item not found")
    if body.quantity > item.quantity:
        raise HTTPException(409, "Inspection quantity exceeds ordered quantity")
    return order, item


def view(row):
    return {
        "id": row.id,
        "item_id": row.item_id,
        "quantity": row.quantity,
        "status": row.status,
        "revision": row.revision,
        "verdict": row.verdict,
        "finding": row.finding,
        "reason": row.reason,
        "result_note": row.result_note,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "data_source": "operator_recorded_demo",
        "external_mode": "simulation",
    }


def lookup(session, actor, body, *, lock=False):
    order, item = binding(session, actor, body, lock=lock)
    query = (
        select(QualityInspection)
        .where(
            QualityInspection.organization_id == actor.organization_id,
            QualityInspection.item_id == item.id,
            QualityInspection.quantity == body.quantity,
        )
        .order_by(QualityInspection.created_at.desc(), QualityInspection.id.desc())
        .limit(1)
    )
    row = session.scalar(query.with_for_update() if lock else query)
    return {
        "order_number": order.number,
        "item_id": item.id,
        "quantity": body.quantity,
        "inspection": view(row) if row else None,
        "known": bool(row and row.status == "completed" and row.verdict in {"passed", "failed"}),
        "data_source": "operator_recorded_demo",
        "external_mode": "simulation",
    }


def record(session, actor, row, action, **details):
    session.add(
        QualityEvent(
            organization_id=actor.organization_id,
            inspection_id=row.id,
            actor_id=actor.id,
            action=action,
            revision=row.revision,
            details=details,
        )
    )
    audit(session, actor, "quality." + action, row.id, revision=row.revision, **details)


def create(session, actor, body, key):
    require_role(actor, "support")
    if not key:
        raise HTTPException(422, "Idempotency-Key is required")
    order, item = binding(session, actor, body, lock=True)
    digest = fingerprint(body.model_dump(mode="json"))
    existing = session.scalar(
        select(QualityInspection)
        .where(
            QualityInspection.organization_id == actor.organization_id,
            QualityInspection.created_by == actor.id,
            QualityInspection.idempotency_key == key,
        )
        .with_for_update()
    )
    if existing:
        if existing.input_hash != digest:
            raise HTTPException(409, "Idempotency key belongs to different input")
        return existing
    latest = lookup(session, actor, body, lock=True)["inspection"]
    if latest and latest["status"] in {"requested", "in_progress"}:
        raise HTTPException(409, "Inspection already active; query existing record")
    row = QualityInspection(
        organization_id=actor.organization_id,
        order_id=order.id,
        item_id=item.id,
        quantity=body.quantity,
        reason=body.reason,
        created_by=actor.id,
        status="requested",
        revision=1,
        idempotency_key=key,
        input_hash=digest,
    )
    session.add(row)
    try:
        session.flush()
        record(session, actor, row, "requested")
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Concurrent inspection creation; query current state") from None
    return row


def owned(session, actor, identifier):
    row = session.scalar(
        select(QualityInspection).where(
            QualityInspection.id == identifier,
            QualityInspection.organization_id == actor.organization_id,
        )
    )
    if not row:
        raise HTTPException(404, "Inspection not found")
    return row


def change(session, actor, identifier, body):
    require_role(actor, *(("admin",) if body.action == "void" else ("approver",)))
    row = owned(session, actor, identifier)
    # Use the same parent/item lock order as return confirmations and new inspections.
    from workbench.models import Order
    from workbench.quality_schemas import QualityLookup

    order = session.get(Order, row.order_id)
    binding(
        session,
        actor,
        QualityLookup(order_number=order.number, item_id=row.item_id, quantity=row.quantity),
        lock=True,
    )
    session.refresh(row)
    if body.action != "void" and row.created_by == actor.id:
        raise HTTPException(403, "Creator cannot inspect own work order")
    expected_status = {"start": "requested", "complete": "in_progress"}.get(body.action)
    if (
        row.revision != body.expected_revision
        or row.status == "voided"
        or (expected_status and row.status != expected_status)
    ):
        raise HTTPException(409, "Inspection state or revision changed")
    if (body.action == "complete") != (body.verdict is not None):
        raise HTTPException(422, "Only completion requires a verdict")
    if body.finding is not None and (
        body.action != "complete"
        or {"defect_confirmed": "failed", "no_defect": "passed", "unclear": "inconclusive"}[
            body.finding
        ]
        != body.verdict
    ):
        raise HTTPException(422, "Explicit finding must match completion verdict")
    if body.action == "complete" and row.handled_by != actor.id:
        raise HTTPException(403, "Only the assigned operator can complete inspection")
    values = {"revision": row.revision + 1, "updated_at": now()}
    if body.action == "start":
        values.update(status="in_progress", handled_by=actor.id)
    elif body.action == "complete":
        values.update(
            status="completed", verdict=body.verdict, finding=body.finding, result_note=body.note
        )
    else:
        values.update(status="voided", verdict=None, finding=None, result_note=body.note)
    changed = session.execute(
        update(QualityInspection)
        .where(
            QualityInspection.id == row.id,
            QualityInspection.revision == body.expected_revision,
            QualityInspection.status == row.status,
        )
        .values(**values),
        execution_options={"synchronize_session": False},
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Concurrent inspection change")
    session.refresh(row)
    record(
        session, actor, row, body.action, note=body.note, verdict=body.verdict, finding=body.finding
    )
    session.commit()
    return row
