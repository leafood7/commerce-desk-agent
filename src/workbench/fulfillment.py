"""Deterministic return-fulfillment lifecycle after a return application is approved.

Logistics, warehouse inspection and refund are simulated local services. Status
transitions are enforced here (never by a model) and every step is idempotent where a
retry could otherwise duplicate a business result (fulfillment creation, refund).
"""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from workbench.domain import audit
from workbench.fulfillment_models import (
    FulfillmentEvent,
    RefundRecord,
    ReturnFulfillment,
    WarehouseInspection,
)
from workbench.models import Order, now
from workbench.return_models import ReturnRequest

# Legal single-step transitions. Enforced by the backend, not by a prompt.
LEGAL = {
    "WAITING_SHIPMENT": {"SHIPPED"},
    "SHIPPED": {"IN_TRANSIT"},
    "IN_TRANSIT": {"DELIVERED"},
    "DELIVERED": {"WAREHOUSE_RECEIVED"},
    "WAREHOUSE_RECEIVED": {"INSPECTION_PASSED", "INSPECTION_FAILED"},
    "INSPECTION_PASSED": {"REFUND_PENDING"},
    "INSPECTION_FAILED": set(),
    "REFUND_PENDING": {"REFUND_PROCESSING"},
    "REFUND_PROCESSING": {"REFUNDED", "REFUND_FAILED", "REFUND_UNKNOWN"},
    "REFUND_UNKNOWN": {"REFUNDED", "REFUND_FAILED"},
    "REFUND_FAILED": {"REFUND_PENDING"},
    "REFUNDED": set(),
}

TERMINAL = {"INSPECTION_FAILED", "REFUNDED"}


def require_role(actor, *roles):
    if actor.role not in roles:
        raise HTTPException(403, "Role is not permitted")


def owned_fulfillment(session, actor, identifier, *, lock=False):
    statement = select(ReturnFulfillment).where(
        ReturnFulfillment.id == identifier,
        ReturnFulfillment.organization_id == actor.organization_id,
    )
    if actor.role not in {"admin", "approver"}:
        statement = statement.where(ReturnFulfillment.customer_id == actor.id)
    row = session.scalar(statement.with_for_update() if lock else statement)
    if not row:
        raise HTTPException(404, "Return fulfillment not found")
    return row


def _event(session, actor, fulfillment, event_type, description, source, source_event_id=None):
    session.add(
        FulfillmentEvent(
            organization_id=actor.organization_id,
            fulfillment_id=fulfillment.id,
            event_type=event_type,
            description=description,
            source=source,
            source_event_id=source_event_id,
        )
    )


def _apply_transition(
    session, actor, fulfillment, target, event_type, description, source="system", source_event_id=None
):
    if target not in LEGAL.get(fulfillment.status, set()):
        raise HTTPException(409, f"Cannot move from {fulfillment.status} to {target}")
    fulfillment.status = target
    fulfillment.version += 1
    fulfillment.updated_at = now()
    _event(session, actor, fulfillment, event_type, description, source, source_event_id=source_event_id)
    audit(session, actor, "fulfillment." + event_type.lower(), fulfillment.id, status=target)


def ensure_for_request(session, actor, request_row):
    """Idempotently create the fulfillment for an approved request (caller commits)."""
    existing = session.scalar(
        select(ReturnFulfillment).where(
            ReturnFulfillment.organization_id == actor.organization_id,
            ReturnFulfillment.return_request_id == request_row.id,
        )
    )
    if existing:
        return existing
    row = ReturnFulfillment(
        organization_id=actor.organization_id,
        return_request_id=request_row.id,
        order_id=request_row.order_id,
        item_id=request_row.item_id,
        customer_id=request_row.created_by,
        status="WAITING_SHIPMENT",
        version=1,
    )
    session.add(row)
    session.flush()
    _event(session, actor, row, "RETURN_CREATED", "退货申请批准后自动创建履约单", "system")
    audit(
        session,
        actor,
        "fulfillment.created",
        row.id,
        return_request_id=request_row.id,
        status="WAITING_SHIPMENT",
    )
    return row


def register_tracking(session, actor, identifier, body):
    require_role(actor, "support")
    row = owned_fulfillment(session, actor, identifier, lock=True)
    _apply_transition(
        session, actor, row, "SHIPPED", "TRACKING_SUBMITTED", f"客户登记寄件 {body.carrier} {body.tracking_number}", "customer"
    )
    row.tracking_number, row.carrier = body.tracking_number, body.carrier
    session.commit()
    return row


def advance_logistics(session, actor, identifier):
    require_role(actor, "admin")
    row = owned_fulfillment(session, actor, identifier, lock=True)
    if row.status == "SHIPPED":
        _apply_transition(session, actor, row, "IN_TRANSIT", "PACKAGE_IN_TRANSIT", "模拟物流：快件已揽收、运输中", "logistics", source_event_id="mock-step-1")
    elif row.status == "IN_TRANSIT":
        _apply_transition(session, actor, row, "DELIVERED", "PACKAGE_DELIVERED", "模拟物流：快件已签收", "logistics", source_event_id="mock-step-2")
    else:
        raise HTTPException(409, "No logistics step is available for the current status")
    session.commit()
    return row


def confirm_receipt(session, actor, identifier):
    require_role(actor, "admin")
    row = owned_fulfillment(session, actor, identifier, lock=True)
    # Logistics "delivered" is a distinct fact from warehouse confirmation.
    _apply_transition(session, actor, row, "WAREHOUSE_RECEIVED", "WAREHOUSE_RECEIVED", "仓库确认收货（与物流签收相互独立）", "warehouse")
    inspection = session.scalar(
        select(WarehouseInspection).where(
            WarehouseInspection.organization_id == actor.organization_id,
            WarehouseInspection.fulfillment_id == row.id,
        ).with_for_update()
    )
    if inspection is None:
        inspection = WarehouseInspection(
            organization_id=actor.organization_id,
            fulfillment_id=row.id,
            operator_id=actor.id,
            inspection_status="received",
        )
        session.add(inspection)
    inspection.received_at = now()
    inspection.updated_at = now()
    session.commit()
    return row


def inspect_goods(session, actor, identifier, body):
    require_role(actor, "admin")
    row = owned_fulfillment(session, actor, identifier, lock=True)
    target = "INSPECTION_PASSED" if body.result == "passed" else "INSPECTION_FAILED"
    _apply_transition(
        session,
        actor,
        row,
        target,
        "INSPECTION_PASSED" if body.result == "passed" else "INSPECTION_FAILED",
        "仓库验收" + ("通过" if body.result == "passed" else "不通过"),
        "warehouse",
    )
    inspection = session.scalar(
        select(WarehouseInspection).where(
            WarehouseInspection.organization_id == actor.organization_id,
            WarehouseInspection.fulfillment_id == row.id,
        ).with_for_update()
    )
    if inspection is None:
        raise HTTPException(409, "Warehouse receipt must be confirmed before inspection")
    inspection.inspection_status = "passed" if body.result == "passed" else "failed"
    inspection.inspection_result = body.result
    inspection.inspection_remark = body.remark or None
    inspection.operator_id = actor.id
    inspection.updated_at = now()
    session.commit()
    return row


def _latest_refund(session, actor, fulfillment):
    return session.scalar(
        select(RefundRecord)
        .where(
            RefundRecord.organization_id == actor.organization_id,
            RefundRecord.fulfillment_id == fulfillment.id,
        )
        .order_by(RefundRecord.requested_at.desc(), RefundRecord.id.desc())
        .limit(1)
    )


def initiate_refund(session, actor, identifier, body):
    require_role(actor, "admin")
    row = owned_fulfillment(session, actor, identifier, lock=True)
    existing_by_key = session.scalar(
        select(RefundRecord).where(
            RefundRecord.organization_id == actor.organization_id,
            RefundRecord.idempotency_key == body.idempotency_key,
        ).with_for_update()
    )
    if existing_by_key:
        if existing_by_key.fulfillment_id != row.id:
            raise HTTPException(409, "Idempotency key belongs to a different fulfillment")
        return existing_by_key  # Idempotent replay, no new refund.
    latest = _latest_refund(session, actor, row)
    if latest is not None:
        if latest.refund_status == "SUCCEEDED":
            raise HTTPException(409, "Refund already recorded; do not re-execute")
        if latest.refund_status in {"PENDING", "PROCESSING"}:
            raise HTTPException(409, "Refund is still in progress")
        if latest.refund_status == "UNKNOWN":
            raise HTTPException(409, "Refund result is unknown; reconcile before retrying")
        # FAILED is a confirmed non-execution and may be retried with a fresh idempotency key.
    request = session.get(ReturnRequest, row.return_request_id)
    if request is None:
        raise HTTPException(409, "Return request is unavailable")
    record = RefundRecord(
        organization_id=actor.organization_id,
        fulfillment_id=row.id,
        refund_no="REF-" + uuid4().hex[:16].upper(),
        refund_amount_cents=request.goods_value_cents,
        refund_status="PENDING",
        simulated_outcome=body.outcome,
        idempotency_key=body.idempotency_key,
    )
    session.add(record)
    session.flush()
    _apply_transition(session, actor, row, "REFUND_PENDING", "REFUND_SUBMITTED", "发起模拟退款（不执行真实退款）")
    _apply_transition(session, actor, row, "REFUND_PROCESSING", "REFUND_PROCESSING", "模拟退款处理中")
    if body.timeout:
        record.refund_status = "UNKNOWN"
        _apply_transition(session, actor, row, "REFUND_UNKNOWN", "REFUND_RESULT_UNKNOWN", "模拟退款超时，结果待核验")
    elif body.outcome == "succeeded":
        record.refund_status, record.completed_at = "SUCCEEDED", now()
        _apply_transition(session, actor, row, "REFUNDED", "REFUND_SUCCEEDED", "模拟退款成功")
    else:
        record.refund_status, record.completed_at = "FAILED", now()
        _apply_transition(session, actor, row, "REFUND_FAILED", "REFUND_FAILED", "模拟退款失败（未执行）")
    session.commit()
    return record


def reconcile_refund(session, actor, identifier):
    require_role(actor, "admin")
    row = owned_fulfillment(session, actor, identifier, lock=True)
    record = _latest_refund(session, actor, row)
    if record is None or record.refund_status != "UNKNOWN":
        raise HTTPException(409, "No unknown refund result to reconcile")
    if row.status != "REFUND_UNKNOWN":
        raise HTTPException(409, "Fulfillment is not awaiting refund reconciliation")
    if record.simulated_outcome == "succeeded":
        record.refund_status, record.completed_at = "SUCCEEDED", now()
        _apply_transition(session, actor, row, "REFUNDED", "REFUND_SUCCEEDED", "核验后确认退款成功")
    else:
        record.refund_status, record.completed_at = "FAILED", now()
        _apply_transition(session, actor, row, "REFUND_FAILED", "REFUND_FAILED", "核验后确认退款未执行")
    session.commit()
    return record


def fulfillment_view(session, actor, row):
    events = session.scalars(
        select(FulfillmentEvent)
        .where(
            FulfillmentEvent.fulfillment_id == row.id,
            FulfillmentEvent.organization_id == actor.organization_id,
        )
        .order_by(FulfillmentEvent.created_at, FulfillmentEvent.id)
    ).all()
    inspection = session.scalar(
        select(WarehouseInspection).where(
            WarehouseInspection.fulfillment_id == row.id,
            WarehouseInspection.organization_id == actor.organization_id,
        )
    )
    refund = _latest_refund(session, actor, row)
    request = session.get(ReturnRequest, row.return_request_id)
    order = session.get(Order, row.order_id)
    return {
        "id": row.id,
        "return_request_id": row.return_request_id,
        "order_number": order.number if order else None,
        "order_id": row.order_id,
        "item_id": row.item_id,
        "status": row.status,
        "tracking_number": row.tracking_number,
        "carrier": row.carrier,
        "version": row.version,
        "goods_value_cents": request.goods_value_cents if request else None,
        "external_mode": "simulation",
        "actual_refund_executed": False,
        "inspection": (
            {
                "inspection_status": inspection.inspection_status,
                "inspection_result": inspection.inspection_result,
                "inspection_remark": inspection.inspection_remark,
                "received_at": inspection.received_at,
            }
            if inspection
            else None
        ),
        "refund": (
            {
                "refund_no": refund.refund_no,
                "refund_amount_cents": refund.refund_amount_cents,
                "refund_status": refund.refund_status,
                "requested_at": refund.requested_at,
                "completed_at": refund.completed_at,
            }
            if refund
            else None
        ),
        "timeline": [
            {
                "event_type": e.event_type,
                "description": e.description,
                "source": e.source,
                "source_event_id": e.source_event_id,
                "created_at": e.created_at,
            }
            for e in events
        ],
    }