"""Policy conflict candidates and human adjudication records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0016_policy_conflicts"
down_revision = "0015_after_sales_evidence_review"
branch_labels = depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    op.create_table(
        "wb_policy_conflicts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column("conflict_type", sa.String(64), nullable=False),
        sa.Column("rule_ids", sa.JSON, nullable=False),
        sa.Column("version_ids", sa.JSON, nullable=False),
        sa.Column("evidence_refs", sa.JSON, nullable=False),
        sa.Column("reasons", sa.JSON, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_review"),
        sa.Column("resolution", sa.JSON),
        sa.Column("rule_signature", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("updated_at", moment, nullable=False),
    )
    op.create_index(
        "ix_wb_conflict_list",
        "wb_policy_conflicts",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "ix_wb_conflict_rules",
        "wb_policy_conflicts",
        ["organization_id", "rule_signature", "status"],
    )
    op.create_table(
        "wb_policy_conflict_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "conflict_id",
            sa.String(36),
            sa.ForeignKey("wb_policy_conflicts.id"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("preferred_rule_id", sa.String(120)),
        sa.Column("resolution_note", sa.Text, nullable=False),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("created_at", moment, nullable=False),
    )
    op.create_index(
        "ix_wb_conflict_review",
        "wb_policy_conflict_reviews",
        ["organization_id", "conflict_id", "created_at"],
    )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM wb_policy_conflict_reviews")):
        raise RuntimeError("Policy conflict adjudications require an explicit retention decision")
    if bind.scalar(sa.text("SELECT COUNT(*) FROM wb_policy_conflicts")):
        raise RuntimeError("Policy conflict records require an explicit retention decision")
    op.drop_table("wb_policy_conflict_reviews")
    op.drop_table("wb_policy_conflicts")