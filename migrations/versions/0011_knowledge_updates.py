"""Durable knowledge publication impacts and follow-up maintenance tasks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0011_knowledge_updates"
down_revision = "0010_policy_extractions"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "wb_knowledge_updates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("wb_documents.id"), nullable=False),
        sa.Column("previous_version_id", sa.String(36), sa.ForeignKey("wb_document_versions.id")),
        sa.Column("current_version_id", sa.String(36), sa.ForeignKey("wb_document_versions.id")),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False
        ),
    )
    op.create_index(
        "ix_wb_update_document",
        "wb_knowledge_updates",
        ["organization_id", "document_id", "created_at"],
    )
    op.create_table(
        "wb_knowledge_update_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "update_id", sa.String(36), sa.ForeignKey("wb_knowledge_updates.id"), nullable=False
        ),
        sa.Column("resource_kind", sa.String(24), nullable=False),
        sa.Column("resource_id", sa.String(80), nullable=False),
        sa.Column("target_version_id", sa.String(36), sa.ForeignKey("wb_document_versions.id")),
        sa.Column("replacement_id", sa.String(80)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("wb_users.id")),
        sa.Column(
            "created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False
        ),
    )
    op.create_index(
        "ix_wb_update_task_pending",
        "wb_knowledge_update_tasks",
        ["organization_id", "status", "updated_at"],
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM wb_knowledge_updates")):
        raise RuntimeError("Retain knowledge maintenance history before downgrade")
    op.drop_table("wb_knowledge_update_tasks")
    op.drop_table("wb_knowledge_updates")
