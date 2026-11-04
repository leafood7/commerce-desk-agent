"""Return fulfillment state machine, event timeline, warehouse inspection and simulated refund."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0013_return_fulfillment"
down_revision = "0012_request_traces"
branch_labels = depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    op.create_table(
        "wb_return_fulfillments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "return_request_id", sa.String(36), sa.ForeignKey("wb_return_requests.id"), nullable=False
        ),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("wb_orders.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("wb_order_items.id"), nullable=False),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("tracking_number", sa.String(64)),
        sa.Column("carrier", sa.String(64)),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("updated_at", moment, nullable=False),
        sa.UniqueConstraint("return_request_id", name="uq_wb_fulfillment_request"),
    )
    op.create_index(
        "ix_wb_fulfillment_list",
        "wb_return_fulfillments",
        ["organization_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_wb_fulfillment_order", "wb_return_fulfillments", ["organization_id", "order_id"]
    )

    op.create_table(
        "wb_fulfillment_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "fulfillment_id",
            sa.String(36),
            sa.ForeignKey("wb_return_fulfillments.id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_event_id", sa.String(64)),
        sa.Column("created_at", moment, nullable=False),
    )
    op.create_index(
        "ix_wb_fulfillment_events_fid", "wb_fulfillment_events", ["fulfillment_id", "created_at"]
    )

    op.create_table(
        "wb_warehouse_inspections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "fulfillment_id",
            sa.String(36),
            sa.ForeignKey("wb_return_fulfillments.id"),
            nullable=False,
        ),
        sa.Column("operator_id", sa.String(36), sa.ForeignKey("wb_users.id")),
        sa.Column("received_at", moment),
        sa.Column("inspection_status", sa.String(24), nullable=False),
        sa.Column("inspection_result", sa.String(24)),
        sa.Column("inspection_remark", sa.String(500)),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("updated_at", moment, nullable=False),
        sa.UniqueConstraint("fulfillment_id", name="uq_wb_warehouse_fulfillment"),
    )
    op.create_index(
        "ix_wb_warehouse_status", "wb_warehouse_inspections", ["organization_id", "inspection_status"]
    )

    op.create_table(
        "wb_refund_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "fulfillment_id",
            sa.String(36),
            sa.ForeignKey("wb_return_fulfillments.id"),
            nullable=False,
        ),
        sa.Column("refund_no", sa.String(64), nullable=False),
        sa.Column("refund_amount_cents", sa.BigInteger, nullable=False),
        sa.Column("refund_status", sa.String(24), nullable=False),
        sa.Column("simulated_outcome", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("requested_at", moment, nullable=False),
        sa.Column("completed_at", moment),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_wb_refund_idem"),
        sa.UniqueConstraint("refund_no", name="uq_wb_refund_no"),
    )
    op.create_index(
        "ix_wb_refund_fulfillment", "wb_refund_records", ["organization_id", "fulfillment_id"]
    )


def downgrade():
    bind = op.get_bind()
    for name in (
        "wb_refund_records",
        "wb_warehouse_inspections",
        "wb_fulfillment_events",
        "wb_return_fulfillments",
    ):
        if bind.scalar(sa.text("SELECT COUNT(*) FROM " + name)):
            raise RuntimeError(
                "Fulfillment audit data requires an explicit retention/rollback decision"
            )
    op.drop_table("wb_refund_records")
    op.drop_table("wb_warehouse_inspections")
    op.drop_table("wb_fulfillment_events")
    op.drop_table("wb_return_fulfillments")