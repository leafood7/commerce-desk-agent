"""Authenticated request and durable execution trace linkage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0012_request_traces"
down_revision = "0011_knowledge_updates"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "wb_traces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("run_id", sa.String(36)),
        sa.Column("job_id", sa.String(36)),
        sa.Column("dispatch_parent_id", sa.String(36)),
        sa.Column("operation", sa.String(120), nullable=False),
        sa.Column(
            "created_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False
        ),
    )
    op.create_index(
        "ix_wb_trace_owner_created", "wb_traces", ["organization_id", "owner_id", "created_at"]
    )
    op.create_index("ix_wb_trace_run", "wb_traces", ["run_id"])
    op.create_index("ix_wb_trace_job", "wb_traces", ["job_id"])
    op.create_table(
        "wb_trace_spans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trace_id", sa.String(36), sa.ForeignKey("wb_traces.id"), nullable=False),
        sa.Column("parent_id", sa.String(36)),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False
        ),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
    )
    op.create_index("ix_wb_trace_span_trace", "wb_trace_spans", ["trace_id", "started_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM wb_traces")):
        raise RuntimeError("Retain diagnostic history before downgrade")
    op.drop_table("wb_trace_spans")
    op.drop_table("wb_traces")
