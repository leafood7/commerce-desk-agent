"""Add demonstration inspection records and immutable transition events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0008_quality_inspections"
down_revision = "0007_return_applications"
branch_labels = None
depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    op.create_table(
        "wb_quality_inspections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("wb_orders.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("wb_order_items.id"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("handled_by", sa.String(36), sa.ForeignKey("wb_users.id")),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("verdict", sa.String(24)),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("result_note", sa.String(500)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("updated_at", moment, nullable=False),
        sa.UniqueConstraint(
            "organization_id", "created_by", "idempotency_key", name="uq_wb_quality_key"
        ),
        sa.CheckConstraint("quantity > 0 AND revision > 0", name="ck_wb_quality_values"),
    )
    op.create_index(
        "ix_wb_quality_item",
        "wb_quality_inspections",
        ["organization_id", "item_id", "quantity", "created_at"],
    )
    op.create_table(
        "wb_quality_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "inspection_id",
            sa.String(36),
            sa.ForeignKey("wb_quality_inspections.id"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("created_at", moment, nullable=False),
    )
    op.create_index("ix_wb_quality_events_inspection_id", "wb_quality_events", ["inspection_id"])


def downgrade():
    bind = op.get_bind()
    for name in ("wb_quality_events", "wb_quality_inspections"):
        if bind.scalar(sa.text("SELECT COUNT(*) FROM " + name)):
            raise RuntimeError("Inspection audit data requires an explicit retention/rollback decision")
    op.drop_table("wb_quality_events")
    op.drop_table("wb_quality_inspections")
