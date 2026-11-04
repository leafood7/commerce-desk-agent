"""Evidence processing and human review columns on after-sales attachments."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0015_after_sales_evidence_review"
down_revision = "0014_after_sales_attachments"
branch_labels = depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    with op.batch_alter_table("wb_after_sales_attachments") as batch:
        batch.add_column(
            sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_REVIEW")
        )
        batch.add_column(sa.Column("extracted_data", sa.JSON))
        batch.add_column(sa.Column("extraction_method", sa.String(32)))
        batch.add_column(sa.Column("extraction_error", sa.String(300)))
        batch.add_column(
            sa.Column(
                "reviewed_by",
                sa.String(36),
                sa.ForeignKey("wb_users.id", name="fk_wb_attachment_reviewed_by"),
            )
        )
        batch.add_column(sa.Column("review_note", sa.Text))
        batch.add_column(sa.Column("reviewed_at", moment))


def downgrade():
    with op.batch_alter_table("wb_after_sales_attachments") as batch:
        batch.drop_column("reviewed_at")
        batch.drop_column("review_note")
        batch.drop_column("reviewed_by")
        batch.drop_column("extraction_error")
        batch.drop_column("extraction_method")
        batch.drop_column("extracted_data")
        batch.drop_column("status")