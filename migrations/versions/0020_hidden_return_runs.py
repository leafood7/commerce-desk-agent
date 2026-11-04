"""Hide return runs from the default recovery selector without deleting history."""

import sqlalchemy as sa
from alembic import op

revision = "0020_hidden_return_runs"
down_revision = "0019_reply_draft_audit"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "wb_runs",
        sa.Column(
            "hidden_from_recovery",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("wb_runs", "hidden_from_recovery")
