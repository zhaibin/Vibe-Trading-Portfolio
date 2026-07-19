"""Release legacy refresh claims that predate durable run association."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260719_0006"
down_revision: str | Sequence[str] | None = "20260719_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE quote_refresh_runs SET scope_json = NULL WHERE status != 'running'")
    op.execute(
        "DELETE FROM idempotency_records "
        "WHERE scope = 'market-data:refresh' "
        "AND state = 'pending' "
        "AND resource_id IS NULL "
        "AND response_status IS NULL"
    )


def downgrade() -> None:
    """Deleted unlinked claims have no resource state that can be reconstructed."""
