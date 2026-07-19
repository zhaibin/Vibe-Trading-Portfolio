"""Add exact historical position snapshots for idempotent replay."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0004"
down_revision: str | Sequence[str] | None = "20260719_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "position_versions",
        sa.Column("position_id", sa.String(length=36), sa.ForeignKey("positions.id"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("instrument_id", sa.String(length=36), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("average_cost", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_position_versions_version"),
    )
    op.execute(
        "INSERT INTO position_versions "
        "(position_id, version, account_id, instrument_id, quantity, average_cost, note, "
        "created_at, updated_at, archived_at) "
        "SELECT id, version, account_id, instrument_id, quantity, average_cost, note, "
        "created_at, updated_at, archived_at FROM positions"
    )


def downgrade() -> None:
    op.drop_table("position_versions")
