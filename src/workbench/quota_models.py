"""Admission mutex and per-run durable usage; no bearer tokens or prompts."""

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import Base


class AdmissionGate(Base):
    # Intentionally no user/org FK: workers must never need this admission-only lock.
    __tablename__ = "wb_admission_gates"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)


class RunUsage(Base):
    __tablename__ = "wb_run_usage"
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_runs.id"), primary_key=True)
    tool_attempts: Mapped[int] = mapped_column(Integer, default=0)
    model_attempts: Mapped[int] = mapped_column(Integer, default=0)
    model_reserved_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model_observed_tokens: Mapped[int] = mapped_column(Integer, default=0)
    unknown_model_attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_tools: Mapped[int] = mapped_column(Integer)
    max_model_attempts: Mapped[int] = mapped_column(Integer)
    max_model_tokens: Mapped[int] = mapped_column(Integer)
    legacy_estimate: Mapped[bool] = mapped_column(Boolean, default=False)
