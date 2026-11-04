"""After-sales evidence attachments: immutable uploaded files scoped by organization.

These are plain file records for human review. They do not feed any OCR, policy
conflict detection or reply drafting, and do not change return approval rules.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, Scoped, identifier, now


class AfterSalesAttachment(Scoped, Base):
    __tablename__ = "wb_after_sales_attachments"
    __table_args__ = (
        Index("ix_wb_attachment_list", "organization_id", "created_at"),
        Index("ix_wb_attachment_return", "organization_id", "return_request_id"),
        Index("ix_wb_attachment_order", "organization_id", "order_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    filename: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32), default="other")
    return_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("wb_return_requests.id")
    )
    order_id: Mapped[str | None] = mapped_column(ForeignKey("wb_orders.id"))
    note: Mapped[str | None] = mapped_column(String(500))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    extracted_data: Mapped[dict | None] = mapped_column(JSON)
    extraction_method: Mapped[str | None] = mapped_column(String(32))
    extraction_error: Mapped[str | None] = mapped_column(String(300))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("wb_users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)