"""Customer reply drafts built from verified facts and confirmed by a human."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, Scoped, identifier, now


class ReplyDraft(Scoped, Base):
    __tablename__ = "wb_reply_drafts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "return_request_id", "version", name="uq_wb_reply_version"
        ),
        Index("ix_wb_reply_list", "organization_id", "return_request_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("wb_conversations.id"))
    return_request_id: Mapped[str] = mapped_column(ForeignKey("wb_return_requests.id"))
    content: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    facts_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    facts_snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("wb_users.id"))
    generation_mode: Mapped[str] = mapped_column(String(24), default="template")
    model_name: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    validation_status: Mapped[str] = mapped_column(String(16), default="unknown")
    validation_errors: Mapped[list | None] = mapped_column(JSON)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(200))
    edited_by_human: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)