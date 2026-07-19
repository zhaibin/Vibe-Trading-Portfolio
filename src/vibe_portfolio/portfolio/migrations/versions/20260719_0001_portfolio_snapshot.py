"""Create the initial exact current-position portfolio snapshot schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def utc_text() -> sa.Text:
    """Return the reviewed SQLite representation for aware UTC timestamps."""
    return sa.Text()


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("cash_balance", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", utc_text(), nullable=False),
        sa.Column("updated_at", utc_text(), nullable=False),
        sa.Column("archived_at", utc_text(), nullable=True),
        sa.CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_accounts_currency"),
        sa.CheckConstraint("version >= 1", name="ck_accounts_version"),
        sa.CheckConstraint("archived_at IS NULL OR archived_at >= created_at", name="ck_accounts_archived_at"),
    )
    op.create_index(
        "uq_accounts_active_normalized_name",
        "accounts",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
    )
    op.create_table(
        "instruments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("canonical_symbol", sa.String(length=21), nullable=False, unique=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("market", sa.String(length=5), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("asset_type", sa.String(length=6), nullable=False),
        sa.Column("created_at", utc_text(), nullable=False),
        sa.Column("updated_at", utc_text(), nullable=False),
        sa.CheckConstraint("market IN ('CN_SH', 'CN_SZ', 'CN_BJ', 'HK', 'US')", name="ck_instruments_market"),
        sa.CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_instruments_currency"),
        sa.CheckConstraint("asset_type IN ('equity', 'etf')", name="ck_instruments_asset_type"),
    )
    op.create_table(
        "instrument_provider_symbols",
        sa.Column("instrument_id", sa.String(length=36), sa.ForeignKey("instruments.id"), primary_key=True),
        sa.Column("provider", sa.String(length=32), primary_key=True),
        sa.Column("provider_symbol", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("instrument_id", "provider", name="uq_instrument_provider_symbols_instrument_provider"),
        sa.UniqueConstraint("provider", "provider_symbol", name="uq_instrument_provider_symbols_provider_symbol"),
    )
    op.create_table(
        "quote_refresh_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("started_at", utc_text(), nullable=False),
        sa.Column("finished_at", utc_text(), nullable=True),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unavailable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'partial', 'failed')", name="ck_quote_refresh_runs_status"
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="ck_quote_refresh_runs_finished_at"
        ),
    )
    op.create_index("ix_quote_refresh_runs_scope_hash", "quote_refresh_runs", ["scope_hash"])
    op.create_index("ix_quote_refresh_runs_finished_at", "quote_refresh_runs", ["finished_at"])
    op.create_table(
        "positions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("instrument_id", sa.String(length=36), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("average_cost", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", utc_text(), nullable=False),
        sa.Column("updated_at", utc_text(), nullable=False),
        sa.Column("archived_at", utc_text(), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_positions_version"),
        sa.CheckConstraint("archived_at IS NULL OR archived_at >= created_at", name="ck_positions_archived_at"),
    )
    op.create_index(
        "uq_positions_active_account_instrument",
        "positions",
        ["account_id", "instrument_id"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
    )
    op.create_index("ix_positions_account_archived", "positions", ["account_id", "archived_at"])
    op.create_table(
        "latest_quotes",
        sa.Column("instrument_id", sa.String(length=36), sa.ForeignKey("instruments.id"), primary_key=True),
        sa.Column("price", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_symbol", sa.String(length=64), nullable=False),
        sa.Column("as_of", utc_text(), nullable=False),
        sa.Column("fetched_at", utc_text(), nullable=False),
        sa.Column("refresh_run_id", sa.String(length=36), sa.ForeignKey("quote_refresh_runs.id"), nullable=False),
        sa.CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_latest_quotes_currency"),
    )
    op.create_table(
        "quote_refresh_items",
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("quote_refresh_runs.id"), primary_key=True),
        sa.Column("instrument_id", sa.String(length=36), sa.ForeignKey("instruments.id"), primary_key=True),
        sa.Column("outcome", sa.String(length=11), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", utc_text(), nullable=False),
        sa.UniqueConstraint("run_id", "instrument_id", name="uq_quote_refresh_items_run_instrument"),
        sa.CheckConstraint("outcome IN ('updated', 'stale', 'unavailable')", name="ck_quote_refresh_items_outcome"),
    )
    op.create_index("ix_quote_refresh_items_created_at", "quote_refresh_items", ["created_at"])
    op.create_table(
        "instrument_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("canonical_symbol", sa.String(length=21), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("market", sa.String(length=5), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("asset_type", sa.String(length=6), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_symbols_json", sa.Text(), nullable=False),
        sa.Column("created_at", utc_text(), nullable=False),
        sa.Column("expires_at", utc_text(), nullable=False),
        sa.Column("consumed_at", utc_text(), nullable=True),
        sa.CheckConstraint("market IN ('CN_SH', 'CN_SZ', 'CN_BJ', 'HK', 'US')", name="ck_instrument_candidates_market"),
        sa.CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_instrument_candidates_currency"),
        sa.CheckConstraint("asset_type IN ('equity', 'etf')", name="ck_instrument_candidates_asset_type"),
        sa.CheckConstraint("expires_at > created_at", name="ck_instrument_candidates_expires_at"),
    )
    op.create_index("ix_instrument_candidates_expires_at", "instrument_candidates", ["expires_at"])
    op.create_table(
        "idempotency_records",
        sa.Column("scope", sa.String(length=96), primary_key=True),
        sa.Column("key_hash", sa.String(length=64), primary_key=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=9), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("created_at", utc_text(), nullable=False),
        sa.Column("expires_at", utc_text(), nullable=False),
        sa.UniqueConstraint("scope", "key_hash", name="uq_idempotency_records_scope_key_hash"),
        sa.CheckConstraint("length(key_hash) = 64", name="ck_idempotency_records_key_hash"),
        sa.CheckConstraint("length(request_hash) = 64", name="ck_idempotency_records_request_hash"),
        sa.CheckConstraint("state IN ('pending', 'completed')", name="ck_idempotency_records_state"),
        sa.CheckConstraint("expires_at > created_at", name="ck_idempotency_records_expires_at"),
    )
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_table("instrument_candidates")
    op.drop_table("quote_refresh_items")
    op.drop_table("latest_quotes")
    op.drop_table("positions")
    op.drop_table("quote_refresh_runs")
    op.drop_table("instrument_provider_symbols")
    op.drop_table("instruments")
    op.drop_table("accounts")
