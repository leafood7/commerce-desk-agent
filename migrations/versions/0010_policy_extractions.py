"""Reviewed policy extraction candidates; additive, no business rule changes."""

import sqlalchemy as sa
from alembic import op

revision = "0010_policy_extractions"
down_revision = "0009_quality_return_basis"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "wb_policy_extractions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "version_id", sa.String(36), sa.ForeignKey("wb_document_versions.id"), nullable=False
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("proposed_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("wb_users.id")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("base_active_id", sa.String(36)),
        sa.Column("review_note", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("version_id", "number", name="uq_wb_extraction_number"),
    )
    op.create_index(
        "ix_wb_policy_extractions_organization_id", "wb_policy_extractions", ["organization_id"]
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM wb_policy_extractions")):
        raise RuntimeError("Retain extraction review history before downgrade")
    op.drop_table("wb_policy_extractions")
