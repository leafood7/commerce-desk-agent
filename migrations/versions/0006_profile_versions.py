"""Safe profile versions and immutable run bindings; additive only."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0006_profile_versions"
down_revision = "0005_admission_usage"
branch_labels = None
depends_on = None


def upgrade():
    moment = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql")
    op.create_table(
        "wb_profile_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), nullable=False
        ),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("wb_users.id"), nullable=False),
        sa.Column("values", sa.JSON, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("created_at", moment, nullable=False),
        sa.Column("published_at", moment),
        sa.UniqueConstraint("organization_id", "number", name="uq_wb_profile_number"),
    )
    op.create_table(
        "wb_profile_heads",
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("wb_organizations.id"), primary_key=True
        ),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("wb_profile_versions.id")),
        sa.Column("revision", sa.Integer, nullable=False),
    )
    op.create_table(
        "wb_run_profiles",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("wb_runs.id"), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("wb_profile_versions.id")),
        sa.Column("values", sa.JSON, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("model_binding_hash", sa.String(64), nullable=False),
        sa.Column("created_at", moment, nullable=False),
    )


def downgrade():
    # Development rollback only; never execute on retained business data without approval.
    op.drop_table("wb_run_profiles")
    op.drop_table("wb_profile_heads")
    op.drop_table("wb_profile_versions")
