"""Durable dispatch without modifying existing business tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0004_run_jobs"
down_revision = "0003_aftersales"
branch_labels = None
depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    op.create_table(
        "wb_run_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("wb_runs.id"), nullable=False),
        sa.Column("action_key", sa.String(128), nullable=False),
        sa.Column("parent_session_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", moment),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("updated_at", moment, nullable=False),
        sa.UniqueConstraint("run_id", "action_key", name="uq_wb_job_action"),
    )
    op.create_index("ix_wb_job_ready", "wb_run_jobs", ["status", "created_at"])
    op.create_index("ix_wb_job_lease", "wb_run_jobs", ["status", "lease_until"])


def downgrade():
    # Development rollback only. Never run against retained job data without approval.
    op.drop_table("wb_run_jobs")
