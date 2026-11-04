"""Link trace roots to the business resource they operated on."""

import sqlalchemy as sa
from alembic import op

revision = "0018_trace_resources"
down_revision = "0017_reply_drafts"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("wb_traces") as batch:
        batch.add_column(sa.Column("resource_kind", sa.String(32)))
        batch.add_column(sa.Column("resource_id", sa.String(64)))
    op.create_index(
        "ix_wb_trace_resource", "wb_traces", ["organization_id", "resource_id"]
    )


def downgrade():
    op.drop_index("ix_wb_trace_resource", table_name="wb_traces")
    with op.batch_alter_table("wb_traces") as batch:
        batch.drop_column("resource_id")
        batch.drop_column("resource_kind")