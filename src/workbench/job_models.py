"""Private durable dispatch state. Never stores bearer tokens or model credentials."""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, identifier, now


class RunJob(Base):
    __tablename__ = "wb_run_jobs"
    __table_args__ = (
        UniqueConstraint("run_id", "action_key", name="uq_wb_job_action"),
        Index("ix_wb_job_ready", "status", "created_at"),
        Index("ix_wb_job_lease", "status", "lease_until"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_runs.id"))
    action_key: Mapped[str] = mapped_column(String(128))
    parent_session_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
