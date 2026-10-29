"""Immutable safe profile versions and per-run snapshots, separate from secret settings."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, identifier, now


class ProfileVersion(Base):
    __tablename__ = "wb_profile_versions"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_wb_profile_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    organization_id: Mapped[str] = mapped_column(ForeignKey("wb_organizations.id"))
    number: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    values: Mapped[dict] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    published_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)


class ProfileHead(Base):
    __tablename__ = "wb_profile_heads"
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("wb_organizations.id"), primary_key=True
    )
    version_id: Mapped[str | None] = mapped_column(ForeignKey("wb_profile_versions.id"))
    revision: Mapped[int] = mapped_column(Integer, default=0)


class RunProfile(Base):
    __tablename__ = "wb_run_profiles"
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_runs.id"), primary_key=True)
    version_id: Mapped[str | None] = mapped_column(ForeignKey("wb_profile_versions.id"))
    values: Mapped[dict] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64))
    model_binding_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
