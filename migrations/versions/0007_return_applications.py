"""Order-line return applications with published policy bindings; additive only."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0007_return_applications"
down_revision = "0006_profile_versions"
branch_labels = None
depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    op.create_table(
        "wb_return_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "version_id", sa.String(36), sa.ForeignKey("wb_document_versions.id"), nullable=False
        ),
        sa.Column("rules", sa.JSON, nullable=False),
        sa.Column("rule_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("created_at", moment, nullable=False),
        sa.UniqueConstraint("organization_id", "rule_hash", name="uq_wb_return_policy"),
    )
    op.create_table(
        "wb_return_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("wb_orders.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("wb_order_items.id"), nullable=False),
        sa.Column(
            "policy_id", sa.String(36), sa.ForeignKey("wb_return_policies.id"), nullable=False
        ),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("item_condition", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("goods_value_cents", sa.BigInteger, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_hash", sa.String(64)),
        sa.Column("decided_by", sa.String(36), sa.ForeignKey("wb_users.id")),
        sa.Column("decision_reason", sa.String(500)),
        sa.Column("receipt_id", sa.String(64), unique=True),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("updated_at", moment, nullable=False),
        sa.UniqueConstraint(
            "organization_id", "created_by", "idempotency_key", name="uq_wb_return_key"
        ),
        sa.CheckConstraint(
            "quantity > 0 AND revision > 0 AND goods_value_cents >= 0", name="ck_wb_return_values"
        ),
    )
    op.create_index(
        "ix_wb_return_item", "wb_return_requests", ["organization_id", "item_id", "status"]
    )
    op.create_index(
        "ix_wb_return_owner", "wb_return_requests", ["organization_id", "created_by", "created_at"]
    )
    op.create_table(
        "wb_return_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column(
            "request_id", sa.String(36), sa.ForeignKey("wb_return_requests.id"), nullable=False
        ),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("created_at", moment, nullable=False),
    )
    op.create_index("ix_wb_return_events_request_id", "wb_return_events", ["request_id"])


def downgrade():
    # Never run a destructive rollback against retained data without user approval.
    op.drop_table("wb_return_events")
    op.drop_table("wb_return_requests")
    op.drop_table("wb_return_policies")
