"""Recorded policy conflict candidates and human adjudication history."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, Scoped, identifier, now


class PolicyConflict(Scoped, Base):
    __tablename__ = "wb_policy_conflicts"
    __table_args__ = (
        Index("ix_wb_conflict_list", "organization_id", "status", "created_at"),
        Index("ix_wb_conflict_rules", "organization_id", "rule_signature", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    conflict_type: Mapped[str] = mapped_column(String(64))
    rule_ids: Mapped[list] = mapped_column(JSON)
    version_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="pending_review")
    resolution: Mapped[dict | None] = mapped_column(JSON)
    rule_signature: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class PolicyConflictReview(Scoped, Base):
    __tablename__ = "wb_policy_conflict_reviews"
    __table_args__ = (
        Index("ix_wb_conflict_review", "organization_id", "conflict_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    conflict_id: Mapped[str] = mapped_column(ForeignKey("wb_policy_conflicts.id"))
    decision: Mapped[str] = mapped_column(String(24))
    preferred_rule_id: Mapped[str | None] = mapped_column(String(120))
    resolution_note: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)