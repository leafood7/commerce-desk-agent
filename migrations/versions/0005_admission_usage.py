"""Admission mutex and durable per-run quotas; additive only."""

import sqlalchemy as sa
from alembic import op

revision = "0005_admission_usage"
down_revision = "0004_run_jobs"
branch_labels = None
depends_on = None


def upgrade():
    gates = op.create_table("wb_admission_gates", sa.Column("key", sa.String(32), primary_key=True))
    op.bulk_insert(gates, [{"key": "runs"}])
    op.create_table(
        "wb_run_usage",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("wb_runs.id"), primary_key=True),
        sa.Column("tool_attempts", sa.Integer, nullable=False),
        sa.Column("model_attempts", sa.Integer, nullable=False),
        sa.Column("model_reserved_tokens", sa.Integer, nullable=False),
        sa.Column("model_observed_tokens", sa.Integer, nullable=False),
        sa.Column("unknown_model_attempts", sa.Integer, nullable=False),
        sa.Column("max_tools", sa.Integer, nullable=False),
        sa.Column("max_model_attempts", sa.Integer, nullable=False),
        sa.Column("max_model_tokens", sa.Integer, nullable=False),
        sa.Column("legacy_estimate", sa.Boolean, nullable=False),
    )
    op.create_index("ix_wb_run_active", "wb_runs", ["status", "created_at"])


def downgrade():
    # Destructive rollback is never part of the deployment command.
    op.drop_index("ix_wb_run_active", table_name="wb_runs")
    op.drop_table("wb_run_usage")
    op.drop_table("wb_admission_gates")
