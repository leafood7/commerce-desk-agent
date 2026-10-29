"""Explicit return branch and operator-confirmed defect finding; additive only."""

import sqlalchemy as sa
from alembic import op

revision = "0009_quality_return_basis"
down_revision = "0008_quality_inspections"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "wb_return_requests",
        sa.Column("return_basis", sa.String(24), nullable=False, server_default="no_reason"),
    )
    op.add_column("wb_quality_inspections", sa.Column("finding", sa.String(24), nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT COUNT(*) FROM wb_return_requests WHERE return_basis <> 'no_reason'")
    ) or bind.scalar(
        sa.text("SELECT COUNT(*) FROM wb_quality_inspections WHERE finding IS NOT NULL")
    ):
        raise RuntimeError("New branch/finding data requires explicit retention decision")
    op.drop_column("wb_quality_inspections", "finding")
    op.drop_column("wb_return_requests", "return_basis")
