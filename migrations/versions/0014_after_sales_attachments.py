"""After-sales evidence attachments (uploaded files for human review, no OCR)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0014_after_sales_attachments"
down_revision = "0013_return_fulfillment"
branch_labels = depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    op.create_table(
        "wb_after_sales_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="other"),
        sa.Column("return_request_id", sa.String(36), sa.ForeignKey("wb_return_requests.id")),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("wb_orders.id")),
        sa.Column("note", sa.String(500)),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("created_at", moment, nullable=False),
    )
    op.create_index(
        "ix_wb_attachment_list", "wb_after_sales_attachments", ["organization_id", "created_at"]
    )
    op.create_index(
        "ix_wb_attachment_return",
        "wb_after_sales_attachments",
        ["organization_id", "return_request_id"],
    )
    op.create_index(
        "ix_wb_attachment_order", "wb_after_sales_attachments", ["organization_id", "order_id"]
    )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM wb_after_sales_attachments")):
        raise RuntimeError("After-sales evidence requires an explicit retention decision")
    op.drop_table("wb_after_sales_attachments")