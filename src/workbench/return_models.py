"""Return applications, immutable rule bindings and simulated acceptance receipts."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, Scoped, identifier, now


class ReturnPolicy(Scoped, Base):
    __tablename__ = "wb_return_policies"
    __table_args__ = (UniqueConstraint("organization_id", "rule_hash", name="uq_wb_return_policy"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    version_id: Mapped[str] = mapped_column(ForeignKey("wb_document_versions.id"))
    rules: Mapped[dict] = mapped_column(JSON)
    rule_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class ReturnRequest(Scoped, Base):
    __tablename__ = "wb_return_requests"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "created_by", "idempotency_key", name="uq_wb_return_key"
        ),
        CheckConstraint(
            "quantity > 0 AND revision > 0 AND goods_value_cents >= 0", name="ck_wb_return_values"
        ),
        Index("ix_wb_return_item", "organization_id", "item_id", "status"),
        Index("ix_wb_return_owner", "organization_id", "created_by", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    order_id: Mapped[str] = mapped_column(ForeignKey("wb_orders.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("wb_order_items.id"))
    policy_id: Mapped[str] = mapped_column(ForeignKey("wb_return_policies.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(500))
    item_condition: Mapped[str] = mapped_column(String(16))
    return_basis: Mapped[str] = mapped_column(
        String(24), default="no_reason", server_default="no_reason"
    )
    status: Mapped[str] = mapped_column(String(24), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    goods_value_cents: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    confirmed_hash: Mapped[str | None] = mapped_column(String(64))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("wb_users.id"))
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    receipt_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class ReturnEvent(Scoped, Base):
    __tablename__ = "wb_return_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    request_id: Mapped[str] = mapped_column(ForeignKey("wb_return_requests.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    action: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class QualityInspection(Scoped, Base):
    __tablename__ = "wb_quality_inspections"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "created_by", "idempotency_key", name="uq_wb_quality_key"
        ),
        CheckConstraint("quantity > 0 AND revision > 0", name="ck_wb_quality_values"),
        Index("ix_wb_quality_item", "organization_id", "item_id", "quantity", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    order_id: Mapped[str] = mapped_column(ForeignKey("wb_orders.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("wb_order_items.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    handled_by: Mapped[str | None] = mapped_column(ForeignKey("wb_users.id"))
    status: Mapped[str] = mapped_column(String(24), default="requested")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    verdict: Mapped[str | None] = mapped_column(String(24))
    finding: Mapped[str | None] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(500))
    result_note: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class QualityEvent(Scoped, Base):
    __tablename__ = "wb_quality_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("wb_quality_inspections.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    action: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
