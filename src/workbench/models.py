"""Business facts and published messages; LangGraph checkpoints are separate."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

UTC_DATETIME = DateTime().with_variant(DATETIME(fsp=6), "mysql")


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def identifier():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "wb_organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    name: Mapped[str] = mapped_column(String(120))


class Scoped:
    organization_id: Mapped[str] = mapped_column(ForeignKey("wb_organizations.id"))


class User(Scoped, Base):
    __tablename__ = "wb_users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_wb_user_email"),
        UniqueConstraint("organization_id", "id", name="uq_wb_user_scope"),
        CheckConstraint("role IN ('admin', 'support', 'approver')", name="ck_wb_user_role"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    email: Mapped[str] = mapped_column(String(254))
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class AuthSession(Base):
    __tablename__ = "wb_auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    expires_at: Mapped[datetime] = mapped_column(UTC_DATETIME, index=True)


class LoginFailure(Base):
    __tablename__ = "wb_login_failures"
    __table_args__ = (
        Index("ix_wb_login_account_time", "account_hash", "created_at"),
        Index("ix_wb_login_address_time", "address_hash", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    account_hash: Mapped[str] = mapped_column(String(64))
    address_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class Conversation(Scoped, Base):
    __tablename__ = "wb_conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "owner_id"], ["wb_users.organization_id", "wb_users.id"]
        ),
        Index("ix_wb_conversation_recent", "organization_id", "owner_id", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class Order(Scoped, Base):
    __tablename__ = "wb_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_wb_order_number"),
        UniqueConstraint("organization_id", "id", name="uq_wb_order_scope"),
        Index("ix_wb_order_list", "organization_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    number: Mapped[str] = mapped_column(String(64))
    customer_name: Mapped[str] = mapped_column(String(120))
    payment_status: Mapped[str] = mapped_column(String(32))
    shipping_status: Mapped[str] = mapped_column(String(64))
    estimated_delivery: Mapped[str | None] = mapped_column(String(200))
    paid_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    promised_ship_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    signed_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    data_source: Mapped[str] = mapped_column(String(32), default="demo_seed")
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class OrderItem(Scoped, Base):
    __tablename__ = "wb_order_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "order_id"], ["wb_orders.organization_id", "wb_orders.id"]
        ),
        CheckConstraint("quantity > 0 AND unit_price_cents >= 0", name="ck_wb_item_amount"),
        Index("ix_wb_item_order", "organization_id", "order_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    order_id: Mapped[str] = mapped_column(String(36))
    sku: Mapped[str] = mapped_column(String(64))
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)


class Run(Scoped, Base):
    __tablename__ = "wb_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "owner_id"], ["wb_users.organization_id", "wb_users.id"]
        ),
        UniqueConstraint(
            "organization_id", "owner_id", "idempotency_key", name="uq_wb_run_idempotency"
        ),
        Index("ix_wb_run_owner", "organization_id", "owner_id", "created_at"),
        Index("ix_wb_run_active", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(String(36))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("wb_conversations.id"))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    input: Mapped[dict] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class Message(Scoped, Base):
    __tablename__ = "wb_messages"
    __table_args__ = (Index("ix_wb_message_conversation", "conversation_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("wb_conversations.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("wb_runs.id"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class RunEvent(Scoped, Base):
    __tablename__ = "wb_run_events"
    __table_args__ = (Index("ix_wb_event_run", "run_id", "id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_runs.id"))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class ShippingRequest(Scoped, Base):
    __tablename__ = "wb_shipping_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "order_id"], ["wb_orders.organization_id", "wb_orders.id"]
        ),
        UniqueConstraint("organization_id", "order_id", "kind", name="uq_wb_shipping_kind"),
        UniqueConstraint(
            "organization_id", "created_by", "idempotency_key", name="uq_wb_shipping_idempotency"
        ),
        CheckConstraint("kind IN ('normal', 'expedite')", name="ck_wb_shipping_kind"),
        Index("ix_wb_shipping_pending", "organization_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    order_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24))
    created_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("wb_users.id"))
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    result_message: Mapped[str] = mapped_column(String(500))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)


class ShippingTicket(Scoped, Base):
    __tablename__ = "wb_shipping_tickets"
    __table_args__ = (Index("ix_wb_ticket_history", "organization_id", "order_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    request_id: Mapped[str] = mapped_column(ForeignKey("wb_shipping_requests.id"), unique=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("wb_orders.id"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="open")
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class AuditRecord(Scoped, Base):
    __tablename__ = "wb_audit_records"
    __table_args__ = (Index("ix_wb_audit_scope", "organization_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    actor_id: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    action: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
