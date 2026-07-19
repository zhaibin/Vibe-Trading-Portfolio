"""Create the initial exact current-position portfolio snapshot schema."""

from collections.abc import Sequence

from alembic import op

from vibe_portfolio.portfolio.tables import Base

revision: str = "20260719_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
