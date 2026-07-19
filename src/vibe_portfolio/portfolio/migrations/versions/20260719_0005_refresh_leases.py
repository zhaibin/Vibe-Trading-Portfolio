"""Add durable ownership and recovery metadata for quote refresh runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0005"
down_revision: str | Sequence[str] | None = "20260719_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quote_refresh_runs", sa.Column("owner_token", sa.String(length=36), nullable=True))
    op.add_column("quote_refresh_runs", sa.Column("lease_expires_at", sa.Text(), nullable=True))
    op.add_column("quote_refresh_runs", sa.Column("scope_json", sa.Text(), nullable=True))
    op.add_column("quote_refresh_runs", sa.Column("terminal_error", sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE quote_refresh_runs SET status = 'failed', finished_at = started_at, "
        "terminal_error = 'REFRESH_ABANDONED' WHERE status = 'running'"
    )
    op.create_index(
        "uq_quote_refresh_runs_single_running",
        "quote_refresh_runs",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_quote_refresh_runs_single_running", table_name="quote_refresh_runs")
    with op.batch_alter_table("quote_refresh_runs") as batch:
        batch.drop_column("terminal_error")
        batch.drop_column("scope_json")
        batch.drop_column("lease_expires_at")
        batch.drop_column("owner_token")
