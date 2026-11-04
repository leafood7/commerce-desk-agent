"""Reply-draft generation and validation audit fields."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0019_reply_draft_audit"
down_revision = "0018_trace_resources"
branch_labels = depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    with op.batch_alter_table("wb_reply_drafts") as batch:
        batch.add_column(
            sa.Column(
                "generation_mode", sa.String(24), nullable=False, server_default="template"
            )
        )
        batch.add_column(sa.Column("model_name", sa.String(80)))
        batch.add_column(sa.Column("prompt_version", sa.String(40)))
        batch.add_column(
            sa.Column(
                "validation_status", sa.String(16), nullable=False, server_default="unknown"
            )
        )
        batch.add_column(sa.Column("validation_errors", sa.JSON))
        batch.add_column(
            sa.Column("fallback_used", sa.Boolean, nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("fallback_reason", sa.String(200)))
        batch.add_column(
            sa.Column("edited_by_human", sa.Boolean, nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("confirmed_at", moment))


def downgrade():
    with op.batch_alter_table("wb_reply_drafts") as batch:
        batch.drop_column("confirmed_at")
        batch.drop_column("edited_by_human")
        batch.drop_column("fallback_reason")
        batch.drop_column("fallback_used")
        batch.drop_column("validation_errors")
        batch.drop_column("validation_status")
        batch.drop_column("prompt_version")
        batch.drop_column("model_name")
        batch.drop_column("generation_mode")