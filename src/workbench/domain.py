"""Deterministic domain operations shared by HTTP and future MCP adapters."""

import hashlib
import json
from datetime import UTC

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from workbench.models import (
    AuditRecord,
    Conversation,
    Order,
    OrderItem,
    ShippingRequest,
    ShippingTicket,
    now,
)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def user_view(user):
    return {
        "id": user.id,
        "organization_id": user.organization_id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "active": user.active,
    }


def audit(session, actor, action, resource_id, **details):
    session.add(
        AuditRecord(
            organization_id=actor.organization_id,
            actor_id=actor.id,
            action=action,
            resource_id=resource_id,
            details=details,
        )
    )


def get_order(session, actor, number, *, lock=False):
    statement = select(Order).where(
        Order.organization_id == actor.organization_id, Order.number == number.strip().upper()
    )
    if lock:
        statement = statement.with_for_update()
    order = session.scalar(statement)
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


def order_view(session, order, items=None):
    if items is None:
        items = session.scalars(
            select(OrderItem)
            .where(
                OrderItem.organization_id == order.organization_id, OrderItem.order_id == order.id
            )
            .order_by(OrderItem.sku)
        ).all()
    return {
        "id": order.id,
        "number": order.number,
        "customer_name": order.customer_name,
        "payment_status": order.payment_status,
        "shipping_status": order.shipping_status,
        "estimated_delivery": order.estimated_delivery,
        "paid_at": order.paid_at,
        "promised_ship_at": order.promised_ship_at.replace(tzinfo=UTC)
        if order.promised_ship_at
        else None,
        "signed_at": order.signed_at,
        "currency": order.currency,
        "data_source": order.data_source,
        "items": [
            {
                "id": item.id,
                "sku": item.sku,
                "product_name": item.product_name,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
            }
            for item in items
        ],
    }


def get_conversation(session, actor, identifier):
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == identifier,
            Conversation.organization_id == actor.organization_id,
            Conversation.owner_id == actor.id,
        )
    )
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    return conversation


def shipping_view(session, request):
    session.refresh(request)
    ticket = session.scalar(
        select(ShippingTicket).where(
            ShippingTicket.request_id == request.id,
            ShippingTicket.organization_id == request.organization_id,
        )
    )
    order = session.get(Order, request.order_id)
    return {
        "id": request.id,
        "order_number": order.number,
        "kind": request.kind,
        "reason": request.reason,
        "status": request.status,
        "created_by": request.created_by,
        "decided_by": request.decided_by,
        "decision_reason": request.decision_reason,
        "result_message": request.result_message,
        "ticket_id": ticket.id if ticket else None,
        "created_at": request.created_at,
        "decided_at": request.decided_at,
        "external_warehouse_notified": False,
    }


def shipping_error(order, kind):
    from business.shipping import eligibility

    data = {
        "payment_status": order.payment_status,
        "shipping_status": order.shipping_status,
        "promised_ship_at": (
            order.promised_ship_at.replace(tzinfo=UTC).isoformat()
            if order.promised_ship_at
            else None
        ),
    }
    return eligibility(data, kind, now().replace(tzinfo=UTC))


def create_shipping(session, actor, body, idempotency_key=None):
    payload_hash = fingerprint(
        {name: getattr(body, name) for name in ("order_number", "kind", "reason")}
    )
    if idempotency_key:
        existing = session.scalar(
            select(ShippingRequest).where(
                ShippingRequest.organization_id == actor.organization_id,
                ShippingRequest.created_by == actor.id,
                ShippingRequest.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.input_hash != payload_hash:
                raise HTTPException(409, "Idempotency key belongs to a different request")
            return existing
    order = get_order(session, actor, body.order_number, lock=True)
    existing = session.scalar(
        select(ShippingRequest).where(
            ShippingRequest.organization_id == actor.organization_id,
            ShippingRequest.order_id == order.id,
            ShippingRequest.kind == body.kind,
        )
    )
    if existing:
        return existing
    error = shipping_error(order, body.kind)
    if error:
        raise HTTPException(409, error)
    request = ShippingRequest(
        organization_id=actor.organization_id,
        order_id=order.id,
        kind=body.kind,
        reason=body.reason,
        status="pending" if body.kind == "expedite" else "completed",
        created_by=actor.id,
        idempotency_key=idempotency_key,
        input_hash=payload_hash,
        result_message=(
            "加急申请待审批，尚未创建工单。"
            if body.kind == "expedite"
            else "已登记普通催单，尚未通知外部仓库。"
        ),
    )
    session.add(request)
    try:
        session.flush()
        if body.kind == "normal":
            session.add(
                ShippingTicket(
                    organization_id=actor.organization_id,
                    request_id=request.id,
                    order_id=order.id,
                    kind=body.kind,
                )
            )
        audit(session, actor, "shipping.requested", request.id, kind=body.kind)
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(ShippingRequest).where(
                ShippingRequest.organization_id == actor.organization_id,
                ShippingRequest.order_id == order.id,
                ShippingRequest.kind == body.kind,
            )
        )
        if existing:
            if (
                idempotency_key
                and existing.idempotency_key == idempotency_key
                and existing.input_hash != payload_hash
            ):
                raise HTTPException(409, "Idempotency key belongs to a different request") from None
            return existing
        raise HTTPException(409, "Conflicting request; query existing status") from None
    return request


def decide_shipping(session, actor, identifier, body):
    target_order = session.scalar(
        select(ShippingRequest.order_id).where(
            ShippingRequest.id == identifier,
            ShippingRequest.organization_id == actor.organization_id,
        )
    )
    if target_order is None:
        raise HTTPException(404, "Request not found")
    # All write paths acquire the order first, then the request.
    order = session.scalar(
        select(Order)
        .where(Order.id == target_order, Order.organization_id == actor.organization_id)
        .with_for_update()
    )
    request = session.scalar(
        select(ShippingRequest)
        .where(
            ShippingRequest.id == identifier,
            ShippingRequest.organization_id == actor.organization_id,
        )
        .with_for_update()
    )
    if request.status != "pending":
        # End MySQL's earlier authentication snapshot before rendering ticket facts.
        session.commit()
        return request
    request.decided_by, request.decided_at = actor.id, now()
    request.decision_reason = body.reason
    if body.action == "reject":
        request.status, request.result_message = "rejected", "已拒绝加急申请，未创建工单。"
    else:
        error = shipping_error(order, "expedite")
        request.status = "invalidated" if error else "completed"
        request.result_message = error or "已创建加急工单，尚未通知外部仓库。"
        if not error:
            session.add(
                ShippingTicket(
                    organization_id=actor.organization_id,
                    request_id=request.id,
                    order_id=order.id,
                    kind="expedite",
                )
            )
    audit(session, actor, "shipping." + body.action, request.id, status=request.status)
    session.commit()
    return request
