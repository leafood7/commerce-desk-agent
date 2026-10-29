"""Deterministic return lifecycle. Rule registration is a human-reviewed mapping.

The demo accepts returns only for explicitly configured SKUs and published clauses.
It does not interpret law, infer receipt from ETA, or execute any refund.
"""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from workbench.domain import audit, fingerprint, get_order
from workbench.knowledge_models import Document, DocumentVersion, KnowledgeChunk
from workbench.knowledge_service import validated_evidence
from workbench.models import Order, OrderItem, now
from workbench.return_models import ReturnEvent, ReturnPolicy, ReturnRequest
from workbench.return_schemas import ReturnInput

RESERVED = ("pending_approval", "approved", "accepted_simulated")


def require_role(actor, *roles):
    if actor.role not in roles:
        raise HTTPException(403, "Role is not permitted")


def policy_evidence(session, actor, policy, *, lock=False):
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == policy.version_id,
            DocumentVersion.organization_id == actor.organization_id,
        )
    )
    if not version or version.policy_scope != "returns":
        raise HTTPException(409, "Return policy scope is unavailable")
    statement = select(Document).where(
        Document.id == version.document_id, Document.organization_id == actor.organization_id
    )
    if lock:
        statement = statement.with_for_update()
    # A current locking read also serializes confirmation with publish/withdraw.
    document = session.scalar(statement)
    if not document or document.active_version_id != version.id:
        raise HTTPException(409, "Return policy publication changed")
    clauses, matches = [], {}
    for name in ("scope_clause", "window_clause", "condition_clause"):
        ref = policy.rules[name]
        chunk = session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.id == ref["chunk_id"],
                KnowledgeChunk.organization_id == actor.organization_id,
                KnowledgeChunk.version_id == version.id,
            )
        )
        if not chunk or ref["quote"] not in chunk.content:
            raise HTTPException(409, "Policy clause is missing or changed")
        matches[chunk.id] = {"id": chunk.id, "version_id": version.id, "content": chunk.content}
        clauses.append({"purpose": name, **ref})
    evidence = validated_evidence(session, actor, list(matches.values()))
    return {"clauses": clauses, "sources": evidence, "mapping": "administrator_verified"}


def register_policy(session, actor, body):
    require_role(actor, "admin")
    rules = body.model_dump(mode="json")
    rules.pop("administrator_verified")
    policy = ReturnPolicy(
        organization_id=actor.organization_id,
        version_id=body.version_id,
        rules=rules,
        rule_hash=fingerprint(rules),
        created_by=actor.id,
    )
    policy_evidence(session, actor, policy, lock=True)
    existing = session.scalar(
        select(ReturnPolicy)
        .where(
            ReturnPolicy.organization_id == actor.organization_id,
            ReturnPolicy.rule_hash == policy.rule_hash,
        )
        .with_for_update()
    )
    if existing:
        return existing
    session.add(policy)
    session.flush()
    audit(session, actor, "return.policy_registered", policy.id, rule_hash=policy.rule_hash)
    session.commit()
    return policy


def scoped_policy(session, actor, identifier):
    policy = session.scalar(
        select(ReturnPolicy).where(
            ReturnPolicy.id == identifier, ReturnPolicy.organization_id == actor.organization_id
        )
    )
    if not policy:
        raise HTTPException(404, "Return policy not found")
    return policy


def owned_request(session, actor, identifier, *, lock=False):
    statement = select(ReturnRequest).where(
        ReturnRequest.id == identifier, ReturnRequest.organization_id == actor.organization_id
    )
    if actor.role not in {"approver", "admin"}:
        statement = statement.where(ReturnRequest.created_by == actor.id)
    row = session.scalar(statement.with_for_update() if lock else statement)
    if not row:
        raise HTTPException(404, "Return request not found")
    return row


def record(session, actor, row, action, **details):
    row.updated_at = now()
    session.add(
        ReturnEvent(
            organization_id=actor.organization_id,
            request_id=row.id,
            actor_id=actor.id,
            action=action,
            revision=row.revision,
            details=details,
        )
    )
    audit(session, actor, "return." + action, row.id, revision=row.revision, **details)


def evaluate(session, actor, body, *, excluding=None, lock=False):
    order = get_order(session, actor, body.order_number, lock=lock)
    statement = select(OrderItem).where(
        OrderItem.id == body.item_id,
        OrderItem.order_id == order.id,
        OrderItem.organization_id == actor.organization_id,
    )
    item = session.scalar(statement.with_for_update() if lock else statement)
    if not item:
        raise HTTPException(404, "Order item not found")
    policy = scoped_policy(session, actor, body.policy_id)
    evidence = policy_evidence(session, actor, policy, lock=lock)
    reservations = select(ReturnRequest).where(
        ReturnRequest.organization_id == actor.organization_id,
        ReturnRequest.item_id == item.id,
        ReturnRequest.status.in_(RESERVED),
    )
    if excluding:
        reservations = reservations.where(ReturnRequest.id != excluding)
    # Locking reads avoid an older authentication snapshot under MySQL RR.
    rows = session.scalars(reservations.with_for_update() if lock else reservations).all()
    remaining = max(0, item.quantity - sum(row.quantity for row in rows))
    missing, blocked = [], []
    applicable = item.sku in policy.rules["skus"]
    if not applicable:
        missing.append("applicable_policy_for_sku")
    if order.signed_at is None:
        missing.append("actual_signed_at")
    elif order.signed_at > now():
        missing.append("verified_signed_at")
    elif applicable and now() > order.signed_at + timedelta(days=policy.rules["window_days"]):
        blocked.append("outside_configured_return_window")
    if order.payment_status != "paid":
        blocked.append("payment_not_paid")
    if applicable and policy.rules["requires_unused"]:
        if body.item_condition == "unknown":
            missing.append("item_condition")
        elif body.item_condition == "used":
            blocked.append("configured_unused_condition_not_met")
    if body.quantity > remaining:
        blocked.append("quantity_exceeds_available")
    facts = {
        "order_id": order.id,
        "item_id": item.id,
        "ordered_quantity": item.quantity,
        "unit_price_cents": item.unit_price_cents,
        "payment_status": order.payment_status,
        "shipping_status": order.shipping_status,
        "signed_at": order.signed_at.isoformat() if order.signed_at else None,
        "currency": order.currency,
        "data_source": order.data_source,
    }
    preview = {
        "application": body.model_dump(mode="json"),
        "facts": facts,
        "rule_hash": policy.rule_hash,
        "available_quantity": remaining,
        "eligibility": "blocked" if blocked else ("needs_information" if missing else "eligible"),
        "missing": missing,
        "blocked": blocked,
        "evidence": evidence,
        "goods_value_cents": item.unit_price_cents * body.quantity,
        "refund_amount_cents": None,
        "external_mode": "simulation",
        "notice": "仅演示商家规则；商品金额为单价乘数量参考值，不是实际退款承诺。",
    }
    preview["preview_hash"] = fingerprint(preview)
    return order, item, preview


def input_for(session, row):
    order = session.get(Order, row.order_id)
    return ReturnInput(
        order_number=order.number,
        item_id=row.item_id,
        policy_id=row.policy_id,
        quantity=row.quantity,
        reason=row.reason,
        item_condition=row.item_condition,
    )


def create_request(session, actor, body, key):
    require_role(actor, "support")
    if not key:
        raise HTTPException(422, "Idempotency-Key is required")
    order = get_order(session, actor, body.order_number, lock=True)
    existing = session.scalar(
        select(ReturnRequest)
        .where(
            ReturnRequest.organization_id == actor.organization_id,
            ReturnRequest.created_by == actor.id,
            ReturnRequest.idempotency_key == key,
        )
        .with_for_update()
    )
    payload_hash = fingerprint(body.model_dump(mode="json"))
    if existing:
        if existing.input_hash != payload_hash:
            raise HTTPException(409, "Idempotency key belongs to different input")
        return existing
    _, item, preview = evaluate(session, actor, body, lock=True)
    if body.quantity > item.quantity:
        raise HTTPException(409, "Quantity exceeds ordered quantity")
    row = ReturnRequest(
        organization_id=actor.organization_id,
        order_id=order.id,
        item_id=item.id,
        policy_id=body.policy_id,
        quantity=body.quantity,
        reason=body.reason,
        item_condition=body.item_condition,
        created_by=actor.id,
        status="draft",
        revision=1,
        goods_value_cents=preview["goods_value_cents"],
        idempotency_key=key,
        input_hash=payload_hash,
    )
    session.add(row)
    try:
        session.flush()
        record(session, actor, row, "drafted")
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Conflicting return request; query existing status") from None
    return row


def locked_request(session, actor, identifier):
    row = owned_request(session, actor, identifier)
    # Consistent write ordering: order -> application -> policy document.
    session.scalar(select(Order).where(Order.id == row.order_id).with_for_update())
    session.expire(row)
    return owned_request(session, actor, identifier, lock=True)


def check_revision(row, revision):
    if row.revision != revision:
        raise HTTPException(409, "Return content changed; reload preview")


def preview_request(session, actor, row, *, lock=False):
    return evaluate(session, actor, input_for(session, row), excluding=row.id, lock=lock)[2]


def correct_request(session, actor, identifier, body):
    require_role(actor, "support")
    row = locked_request(session, actor, identifier)
    check_revision(row, body.expected_revision)
    if row.status != "draft":
        raise HTTPException(409, "Only draft content may be corrected; cancel and reapply")
    if body.application.order_number != session.get(Order, row.order_id).number:
        raise HTTPException(409, "A different order requires a new application")
    _, item, preview = evaluate(session, actor, body.application, excluding=row.id, lock=True)
    if body.application.quantity > item.quantity:
        raise HTTPException(409, "Quantity exceeds ordered quantity")
    row.item_id, row.policy_id = item.id, body.application.policy_id
    row.quantity, row.reason = body.application.quantity, body.application.reason
    row.item_condition = body.application.item_condition
    row.goods_value_cents, row.confirmed_hash = preview["goods_value_cents"], None
    row.revision += 1
    record(session, actor, row, "corrected")
    session.commit()
    return row


def confirm_request(session, actor, identifier, body):
    require_role(actor, "support")
    row = locked_request(session, actor, identifier)
    check_revision(row, body.expected_revision)
    if row.status != "draft":
        if row.confirmed_hash == body.preview_hash and row.status in RESERVED:
            return row
        raise HTTPException(409, "Return application is not a draft")
    preview = preview_request(session, actor, row, lock=True)
    if preview["preview_hash"] != body.preview_hash:
        raise HTTPException(409, "Facts or evidence changed; reload preview")
    if preview["eligibility"] != "eligible":
        raise HTTPException(
            409,
            {
                "message": "Return needs review",
                "missing": preview["missing"],
                "blocked": preview["blocked"],
            },
        )
    row.confirmed_hash, row.status = body.preview_hash, "pending_approval"
    record(session, actor, row, "confirmed", preview_hash=body.preview_hash)
    session.commit()
    return row


def decide_request(session, actor, identifier, body):
    require_role(actor, "approver")
    row = locked_request(session, actor, identifier)
    check_revision(row, body.expected_revision)
    if actor.id == row.created_by:
        raise HTTPException(403, "Independent approval is required")
    desired = "approved" if body.action == "approve" else "rejected"
    if row.status != "pending_approval":
        if row.status == desired and row.decided_by == actor.id:
            return row
        raise HTTPException(409, "Application is not pending approval")
    if body.action == "approve":
        preview = preview_request(session, actor, row, lock=True)
        if preview["eligibility"] != "eligible" or preview["preview_hash"] != row.confirmed_hash:
            raise HTTPException(
                409, "Facts or policy changed after confirmation; reject and reapply"
            )
    row.status, row.decided_by, row.decision_reason = desired, actor.id, body.reason
    record(session, actor, row, desired, reason=body.reason)
    session.commit()
    return row


def submit_simulated(session, actor, identifier, body):
    require_role(actor, "support")
    row = locked_request(session, actor, identifier)
    check_revision(row, body.expected_revision)
    if row.status == "accepted_simulated":
        return row
    if row.status != "approved":
        raise HTTPException(409, "Approved application required")
    preview = preview_request(session, actor, row, lock=True)
    if preview["eligibility"] != "eligible" or preview["preview_hash"] != row.confirmed_hash:
        raise HTTPException(409, "Facts or policy changed; submission blocked")
    # Local deterministic adapter: receipt and business state share one transaction.
    # A response lost after commit is recovered via GET, never a new external action.
    row.receipt_id, row.status = "SIM-RETURN-" + row.id, "accepted_simulated"
    record(session, actor, row, "accepted_simulated", receipt_id=row.receipt_id)
    session.commit()
    return row


def cancel_request(session, actor, identifier, body):
    require_role(actor, "support")
    row = locked_request(session, actor, identifier)
    check_revision(row, body.expected_revision)
    if row.status == "cancelled":
        return row
    if row.status not in {"draft", "pending_approval", "approved"}:
        raise HTTPException(409, "Accepted or rejected applications cannot be cancelled")
    row.status = "cancelled"
    record(session, actor, row, "cancelled")
    session.commit()
    return row


def request_view(session, actor, row):
    events = session.scalars(
        select(ReturnEvent)
        .where(
            ReturnEvent.request_id == row.id, ReturnEvent.organization_id == actor.organization_id
        )
        .order_by(ReturnEvent.created_at, ReturnEvent.id)
    ).all()
    return {
        "id": row.id,
        "order_number": session.get(Order, row.order_id).number,
        "item_id": row.item_id,
        "policy_id": row.policy_id,
        "quantity": row.quantity,
        "reason": row.reason,
        "item_condition": row.item_condition,
        "status": row.status,
        "revision": row.revision,
        "created_by": row.created_by,
        "decided_by": row.decided_by,
        "decision_reason": row.decision_reason,
        "goods_value_cents": row.goods_value_cents,
        "refund_amount_cents": None,
        "receipt_id": row.receipt_id,
        "external_mode": "simulation",
        "actual_refund_executed": False,
        "events": [
            {
                "action": e.action,
                "revision": e.revision,
                "created_at": e.created_at,
                "details": e.details,
            }
            for e in events
        ],
    }
