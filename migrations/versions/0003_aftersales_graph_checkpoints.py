"""Durable after-sales LangGraph checkpoints and idempotent continuations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import LONGBLOB

revision = "0003_aftersales"
down_revision = "0002_knowledge"
branch_labels = None
depends_on = None


def upgrade():
    binary = sa.LargeBinary().with_variant(LONGBLOB(), "mysql")
    op.create_table(
        "wb_graph_checkpoints",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("wb_runs.id"), primary_key=True),
        sa.Column("namespace", sa.String(120), primary_key=True),
        sa.Column("checkpoint_id", sa.String(40), primary_key=True),
        sa.Column("parent_id", sa.String(40)),
        sa.Column("payload_type", sa.String(20), nullable=False),
        sa.Column("payload", binary, nullable=False),
        sa.Column("metadata_type", sa.String(20), nullable=False),
        sa.Column("metadata_payload", binary, nullable=False),
    )
    op.create_table(
        "wb_graph_writes",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("wb_runs.id"), primary_key=True),
        sa.Column("namespace", sa.String(120), primary_key=True),
        sa.Column("checkpoint_id", sa.String(40), primary_key=True),
        sa.Column("task_id", sa.String(40), primary_key=True),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column("channel", sa.String(160), nullable=False),
        sa.Column("payload_type", sa.String(20), nullable=False),
        sa.Column("payload", binary, nullable=False),
    )
    op.create_table(
        "wb_flow_actions",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("wb_runs.id"), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("body", sa.JSON, nullable=False),
    )


def downgrade():
    op.drop_table("wb_flow_actions")
    op.drop_table("wb_graph_writes")
    op.drop_table("wb_graph_checkpoints")
