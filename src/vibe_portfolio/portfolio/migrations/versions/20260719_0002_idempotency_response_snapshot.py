"""Persist immutable idempotent response snapshots without retaining requests."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0002"
down_revision: str | Sequence[str] | None = "20260719_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("idempotency_records", sa.Column("response_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("idempotency_records", "response_json")
