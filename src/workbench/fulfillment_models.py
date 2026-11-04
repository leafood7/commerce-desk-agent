"""Post-approval return fulfillment: a deterministic state machine with an event timeline.

The fulfillment is created after a return application is approved. Logistics, warehouse
inspection and refund are all simulated local services; no real shipment, receipt or
refund is executed. Status transitions are enforced in :mod:`workbench.fulfillment`,
never by a model.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, Scoped, identifier, now


class ReturnFulfillment(Scoped, Base):
    __tablename__ = "wb_return_fulfillments"
    __table_args__ = (
        UniqueConstraint("return_request_id", name="uq_wb_fulfillment_request"),
        Index("ix_wb_fulfillment_list", "organization_id", "status", "updated_at"),
        Index("ix_wb_fulfillment_order", "organization_id", "order_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    return_request_id: Mapped[str] = mapped_column(ForeignKey("wb_return_requests.id"))
    order_id: Mapped[str] = mapped_column(ForeignKey("wb_orders.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("wb_order_items.id"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    status: Mapped[str] = mapped_column(String(24), default="WAITING_SHIPMENT")
    tracking_number: Mapped[str | None] = mapped_column(String(64))
    carrier: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class FulfillmentEvent(Scoped, Base):
    __tablename__ = "wb_fulfillment_events"
    __table_args__ = (Index("ix_wb_fulfillment_events_fid", "fulfillment_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    fulfillment_id: Mapped[str] = mapped_column(ForeignKey("wb_return_fulfillments.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(32), default="system")
    source_event_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class WarehouseInspection(Scoped, Base):
    __tablename__ = "wb_warehouse_inspections"
    __table_args__ = (
        UniqueConstraint("fulfillment_id", name="uq_wb_warehouse_fulfillment"),
        Index("ix_wb_warehouse_status", "organization_id", "inspection_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    fulfillment_id: Mapped[str] = mapped_column(ForeignKey("wb_return_fulfillments.id"))
    operator_id: Mapped[str | None] = mapped_column(ForeignKey("wb_users.id"))
    received_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    inspection_status: Mapped[str] = mapped_column(String(24), default="pending")
    inspection_result: Mapped[str | None] = mapped_column(String(24))
    inspection_remark: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class RefundRecord(Scoped, Base):
    __tablename__ = "wb_refund_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_wb_refund_idem"),
        UniqueConstraint("refund_no", name="uq_wb_refund_no"),
        Index("ix_wb_refund_fulfillment", "organization_id", "fulfillment_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    fulfillment_id: Mapped[str] = mapped_column(ForeignKey("wb_return_fulfillments.id"))
    refund_no: Mapped[str] = mapped_column(String(64))
    refund_amount_cents: Mapped[int] = mapped_column(BigInteger)
    refund_status: Mapped[str] = mapped_column(String(24), default="PENDING")
    simulated_outcome: Mapped[str] = mapped_column(String(16), default="succeeded")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    requested_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)