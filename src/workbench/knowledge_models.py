"""Immutable document versions, durable ingestion work and scoped tool grants."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import UTC_DATETIME, Base, Scoped, identifier, now


class MCPGrant(Base):
    __tablename__ = "wb_mcp_grants"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_session_hash: Mapped[str] = mapped_column(
        ForeignKey("wb_auth_sessions.token_hash", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    audience: Mapped[str] = mapped_column(String(300))
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTC_DATETIME, index=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class Document(Scoped, Base):
    __tablename__ = "wb_documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_wb_document_scope"),
        Index("ix_wb_document_list", "organization_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    title: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    active_version_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class DocumentVersion(Scoped, Base):
    __tablename__ = "wb_document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "document_id"], ["wb_documents.organization_id", "wb_documents.id"]
        ),
        UniqueConstraint("document_id", "number", name="uq_wb_version_number"),
        UniqueConstraint(
            "document_id", "content_hash", "config_hash", name="uq_wb_version_content"
        ),
        UniqueConstraint("organization_id", "id", name="uq_wb_version_scope"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    document_id: Mapped[str] = mapped_column(String(36))
    number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(300))
    content_hash: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    policy_scope: Mapped[str] = mapped_column(String(32), default="unknown")
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_until: Mapped[date | None] = mapped_column(Date)
    chunk_strategy: Mapped[str] = mapped_column(String(24))
    chunk_chars: Mapped[int] = mapped_column(Integer, default=600)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=80)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    parse_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding_revision: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    published_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class KnowledgeChunk(Scoped, Base):
    __tablename__ = "wb_knowledge_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "version_id"],
            ["wb_document_versions.organization_id", "wb_document_versions.id"],
        ),
        UniqueConstraint("version_id", "position", name="uq_wb_chunk_position"),
        Index("ix_wb_chunk_version", "organization_id", "version_id"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(36))
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(500), default="")


class IngestionJob(Scoped, Base):
    __tablename__ = "wb_ingestion_jobs"
    __table_args__ = (Index("ix_wb_ingestion_queue", "status", "lease_until", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    version_id: Mapped[str] = mapped_column(ForeignKey("wb_document_versions.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)


class ToolInvocation(Scoped, Base):
    __tablename__ = "wb_tool_invocations"
    __table_args__ = (Index("ix_wb_tool_audit", "organization_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    actor_id: Mapped[str] = mapped_column(ForeignKey("wb_users.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("wb_runs.id"))
    name: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(80))
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, default=now)
