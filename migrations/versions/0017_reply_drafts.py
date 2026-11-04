"""Human-confirmed customer reply drafts built from verified facts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0017_reply_drafts"
down_revision = "0016_policy_conflicts"
branch_labels = depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    op.create_table(
        "wb_reply_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("wb_conversations.id")),
        sa.Column(
            "return_request_id",
            sa.String(36),
            sa.ForeignKey("wb_return_requests.id"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("evidence_refs", sa.JSON, nullable=False),
        sa.Column("facts_snapshot", sa.JSON, nullable=False),
        sa.Column("facts_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("confirmed_by", sa.String(36), sa.ForeignKey("wb_users.id")),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("updated_at", moment, nullable=False),
        sa.UniqueConstraint(
            "organization_id", "return_request_id", "version", name="uq_wb_reply_version"
        ),
    )
    op.create_index(
        "ix_wb_reply_list",
        "wb_reply_drafts",
        ["organization_id", "return_request_id", "created_at"],
    )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM wb_reply_drafts")):
        raise RuntimeError("Reply drafts require an explicit retention decision")
    op.drop_table("wb_reply_drafts")