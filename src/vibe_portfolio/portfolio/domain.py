"""Exact, provider-independent portfolio domain values."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class DomainValidationError(ValueError):
    """Raised when a domain value cannot be represented exactly."""


class Currency(StrEnum):
    CNY = "CNY"
    HKD = "HKD"
    USD = "USD"


class Market(StrEnum):
    CN_SH = "CN_SH"
    CN_SZ = "CN_SZ"
    CN_BJ = "CN_BJ"
    HK = "HK"
    US = "US"


class AssetType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"


class QuoteState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CanonicalInstrument:
    symbol: str
    name: str
    market: Market
    currency: Currency
    asset_type: AssetType


def _parse_exact(
    value: str | Decimal,
    *,
    scale: int,
    positive: bool,
    maximum: Decimal,
    code: str,
) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise DomainValidationError(f"{code}_invalid") from exc
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0) or parsed > maximum:
        raise DomainValidationError(f"{code}_range")
    exponent = parsed.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > scale:
        raise DomainValidationError(f"{code}_precision")
    return parsed


def canonical_symbol(code: str, market: Market | str) -> str:
    """Return the canonical market-qualified identity for an instrument."""
    try:
        canonical_market = Market(market)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("market_invalid") from exc
    cleaned = code.strip().upper()
    suffix = {
        Market.CN_SH: "SH",
        Market.CN_SZ: "SZ",
        Market.CN_BJ: "BJ",
        Market.HK: "HK",
        Market.US: "US",
    }[canonical_market]
    base = cleaned.removesuffix(f".{suffix}")
    if canonical_market is Market.HK:
        if not base.isdigit() or not 1 <= len(base) <= 5:
            raise DomainValidationError("symbol_invalid")
        return f"{base.zfill(5)}.HK"
    if canonical_market in {Market.CN_SH, Market.CN_SZ, Market.CN_BJ} and not re.fullmatch(r"\d{6}", base):
        raise DomainValidationError("symbol_invalid")
    if canonical_market is Market.US and not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,14}", base):
        raise DomainValidationError("symbol_invalid")
    return f"{base}.{suffix}"


def parse_quantity(value: str | Decimal) -> Decimal:
    return _parse_exact(
        value,
        scale=8,
        positive=True,
        maximum=Decimal("1000000000000"),
        code="quantity",
    )


def parse_money(value: str | Decimal) -> Decimal:
    return _parse_exact(
        value,
        scale=6,
        positive=False,
        maximum=Decimal("1000000000000"),
        code="money",
    )


def parse_price(value: str | Decimal) -> Decimal:
    return _parse_exact(
        value,
        scale=6,
        positive=True,
        maximum=Decimal("1000000000000"),
        code="price",
    )


def quote_state(
    as_of: datetime | None,
    *,
    latest_attempt_succeeded: bool,
    now: datetime,
) -> QuoteState:
    """Derive quote freshness from the newest successful refresh attempt."""
    if as_of is None:
        return QuoteState.UNAVAILABLE
    if not latest_attempt_succeeded or now - as_of > timedelta(hours=72):
        return QuoteState.STALE
    return QuoteState.FRESH
