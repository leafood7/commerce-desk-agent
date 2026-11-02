"""Content-free diagnostic records, scoped to the authenticated operator."""

from sqlalchemy import JSON, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, now


class TraceRecord(Base):
    __tablename__ = "wb_traces"
    __table_args__ = (
        Index("ix_wb_trace_owner_created", "organization_id", "owner_id", "created_at"),
        Index("ix_wb_trace_run", "run_id"),
        Index("ix_wb_trace_job", "job_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("wb_organizations.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    run_id: Mapped[str | None] = mapped_column(String(36))
    job_id: Mapped[str | None] = mapped_column(String(36))
    dispatch_parent_id: Mapped[str | None] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[object] = mapped_column(UTC_DATETIME, default=now)


class TraceSpan(Base):
    __tablename__ = "wb_trace_spans"
    __table_args__ = (Index("ix_wb_trace_span_trace", "trace_id", "started_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("wb_traces.id"))
    parent_id: Mapped[str | None] = mapped_column(String(36))
    service: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON)
    started_at: Mapped[object] = mapped_column(UTC_DATETIME, default=now)
    elapsed_ms: Mapped[int] = mapped_column(Integer)
