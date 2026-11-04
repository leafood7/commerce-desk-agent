"""Thin authenticated fulfillment APIs; MCP and the Agent reuse the same domain functions."""

from fastapi import Depends, Query
from sqlalchemy import select

from workbench import fulfillment
from workbench.fulfillment_models import ReturnFulfillment
from workbench.fulfillment_schemas import (
    ConfirmReceipt,
    InitiateRefund,
    InspectGoods,
    RegisterTracking,
)


def attach_fulfillment_routes(app, session_dependency, actor_dependency, require):
    @app.get("/api/v1/fulfillments")
    def fulfillments(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        statement = select(ReturnFulfillment).where(
            ReturnFulfillment.organization_id == user.organization_id
        )
        if user.role not in {"admin", "approver"}:
            statement = statement.where(ReturnFulfillment.customer_id == user.id)
        rows = session.scalars(
            statement.order_by(ReturnFulfillment.created_at.desc(), ReturnFulfillment.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [fulfillment.fulfillment_view(session, user, row) for row in rows],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/fulfillments/{identifier}")
    def status(
        identifier: str, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        return fulfillment.fulfillment_view(
            session, user, fulfillment.owned_fulfillment(session, user, identifier)
        )

    @app.post("/api/v1/fulfillments/{identifier}/tracking")
    def tracking(
        identifier: str,
        body: RegisterTracking,
        user=Depends(require("support")),
        session=Depends(session_dependency),
    ):
        row = fulfillment.register_tracking(session, user, identifier, body)
        return fulfillment.fulfillment_view(session, user, row)

    @app.post("/api/v1/fulfillments/{identifier}/logistics/advance")
    def logistics_advance(
        identifier: str, user=Depends(require("admin")), session=Depends(session_dependency)
    ):
        row = fulfillment.advance_logistics(session, user, identifier)
        return fulfillment.fulfillment_view(session, user, row)

    @app.post("/api/v1/fulfillments/{identifier}/warehouse/receive")
    def warehouse_receive(
        identifier: str,
        body: ConfirmReceipt,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        row = fulfillment.confirm_receipt(session, user, identifier)
        return fulfillment.fulfillment_view(session, user, row)

    @app.post("/api/v1/fulfillments/{identifier}/warehouse/inspect")
    def warehouse_inspect(
        identifier: str,
        body: InspectGoods,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        row = fulfillment.inspect_goods(session, user, identifier, body)
        return fulfillment.fulfillment_view(session, user, row)

    @app.post("/api/v1/fulfillments/{identifier}/refund/initiate")
    def refund_initiate(
        identifier: str,
        body: InitiateRefund,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        fulfillment.initiate_refund(session, user, identifier, body)
        return fulfillment.fulfillment_view(
            session, user, fulfillment.owned_fulfillment(session, user, identifier)
        )

    @app.post("/api/v1/fulfillments/{identifier}/refund/reconcile")
    def refund_reconcile(
        identifier: str, user=Depends(require("admin")), session=Depends(session_dependency)
    ):
        fulfillment.reconcile_refund(session, user, identifier)
        return fulfillment.fulfillment_view(
            session, user, fulfillment.owned_fulfillment(session, user, identifier)
        )