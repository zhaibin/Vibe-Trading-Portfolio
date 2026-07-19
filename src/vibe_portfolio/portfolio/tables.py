"""Mapped SQLite schema for current-position portfolio snapshots."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from vibe_portfolio.portfolio.persistence_types import ExactDecimal, UtcIsoDateTime


class Base(DeclarativeBase):
    """Base class for portfolio persistence rows."""


class AccountRow(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_accounts_currency"),
        CheckConstraint("version >= 1", name="ck_accounts_version"),
        CheckConstraint("archived_at IS NULL OR archived_at >= created_at", name="ck_accounts_archived_at"),
        Index(
            "uq_accounts_active_normalized_name",
            "normalized_name",
            unique=True,
            sqlite_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cash_balance: Mapped[Decimal | None] = mapped_column(ExactDecimal(), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(UtcIsoDateTime(), nullable=True)


class AccountVersionRow(Base):
    """Append-only account state used to reconstruct an exact historical API view."""

    __tablename__ = "account_versions"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 80", name="ck_account_versions_name"),
        CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_account_versions_currency"),
        CheckConstraint("version >= 1", name="ck_account_versions_version"),
    )

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cash_balance: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class InstrumentRow(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint("market IN ('CN_SH', 'CN_SZ', 'CN_BJ', 'HK', 'US')", name="ck_instruments_market"),
        CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_instruments_currency"),
        CheckConstraint("asset_type IN ('equity', 'etf')", name="ck_instruments_asset_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_symbol: Mapped[str] = mapped_column(String(21), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    market: Mapped[str] = mapped_column(String(5), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)


class InstrumentProviderSymbolRow(Base):
    __tablename__ = "instrument_provider_symbols"
    __table_args__ = (
        UniqueConstraint("instrument_id", "provider", name="uq_instrument_provider_symbols_instrument_provider"),
        UniqueConstraint("provider", "provider_symbol", name="uq_instrument_provider_symbols_provider_symbol"),
    )

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_symbol: Mapped[str] = mapped_column(String(64), nullable=False)


class PositionRow(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_positions_version"),
        CheckConstraint("archived_at IS NULL OR archived_at >= created_at", name="ck_positions_archived_at"),
        Index(
            "uq_positions_active_account_instrument",
            "account_id",
            "instrument_id",
            unique=True,
            sqlite_where=text("archived_at IS NULL"),
        ),
        Index("ix_positions_account_archived", "account_id", "archived_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(UtcIsoDateTime(), nullable=True)


class QuoteRefreshRunRow(Base):
    __tablename__ = "quote_refresh_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'completed', 'partial', 'failed')", name="ck_quote_refresh_runs_status"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_quote_refresh_runs_finished_at"),
        Index("ix_quote_refresh_runs_finished_at", "finished_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UtcIsoDateTime(), nullable=True)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    stale_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    unavailable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


class LatestQuoteRow(Base):
    __tablename__ = "latest_quotes"
    __table_args__ = (CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_latest_quotes_currency"),)

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    price: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    refresh_run_id: Mapped[str] = mapped_column(ForeignKey("quote_refresh_runs.id"), nullable=False)


class QuoteRefreshItemRow(Base):
    __tablename__ = "quote_refresh_items"
    __table_args__ = (
        UniqueConstraint("run_id", "instrument_id", name="uq_quote_refresh_items_run_instrument"),
        CheckConstraint("outcome IN ('updated', 'stale', 'unavailable')", name="ck_quote_refresh_items_outcome"),
        Index("ix_quote_refresh_items_created_at", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("quote_refresh_runs.id"), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(11), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)


class InstrumentCandidateRow(Base):
    __tablename__ = "instrument_candidates"
    __table_args__ = (
        CheckConstraint("market IN ('CN_SH', 'CN_SZ', 'CN_BJ', 'HK', 'US')", name="ck_instrument_candidates_market"),
        CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_instrument_candidates_currency"),
        CheckConstraint("asset_type IN ('equity', 'etf')", name="ck_instrument_candidates_asset_type"),
        CheckConstraint("expires_at > created_at", name="ck_instrument_candidates_expires_at"),
        Index("ix_instrument_candidates_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_symbol: Mapped[str] = mapped_column(String(21), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    market: Mapped[str] = mapped_column(String(5), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(6), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_symbols_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcIsoDateTime(), nullable=True)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "key_hash", name="uq_idempotency_records_scope_key_hash"),
        CheckConstraint("length(key_hash) = 64", name="ck_idempotency_records_key_hash"),
        CheckConstraint("length(request_hash) = 64", name="ck_idempotency_records_request_hash"),
        CheckConstraint("state IN ('pending', 'completed')", name="ck_idempotency_records_state"),
        CheckConstraint("expires_at > created_at", name="ck_idempotency_records_expires_at"),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    scope: Mapped[str] = mapped_column(String(96), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(9), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resource_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcIsoDateTime(), nullable=False)
