"""Provider-independent immutable market-data values."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from vibe_portfolio.portfolio.domain import (
    AssetType,
    Currency,
    DomainValidationError,
    Market,
    canonical_symbol,
    parse_price,
)


class ProviderErrorCode(StrEnum):
    DESTINATION_BLOCKED = "PROVIDER_DESTINATION_BLOCKED"
    TIMEOUT = "PROVIDER_TIMEOUT"
    RESPONSE_TOO_LARGE = "PROVIDER_RESPONSE_TOO_LARGE"
    RESPONSE_INVALID = "QUOTE_RESPONSE_INVALID"
    CURRENCY_MISMATCH = "QUOTE_CURRENCY_MISMATCH"


class ProviderFailure(RuntimeError):
    def __init__(self, code: ProviderErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderSymbol:
    provider: str
    symbol: str


@dataclass(frozen=True, slots=True)
class InstrumentCandidate:
    canonical_symbol: str
    name: str
    market: Market
    currency: Currency
    asset_type: AssetType
    provider_symbols: tuple[ProviderSymbol, ...]
    candidate_id: UUID | None = None

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(mapping.provider for mapping in self.provider_symbols)


@dataclass(frozen=True, slots=True)
class ProviderInstrument:
    canonical_symbol: str
    provider_symbol: str
    market: Market
    currency: Currency
    asset_type: AssetType


@dataclass(frozen=True, slots=True)
class ProviderQuote:
    canonical_symbol: str
    provider_symbol: str
    price: Decimal
    currency: Currency
    as_of: datetime
    provider: str


@dataclass(frozen=True, slots=True)
class RefreshScope:
    instrument_ids: tuple[UUID, ...] | None

    @classmethod
    def all(cls) -> "RefreshScope":
        return cls(instrument_ids=None)


@dataclass(frozen=True, slots=True)
class RefreshResult:
    run_id: UUID
    status: Literal["succeeded", "partial", "failed"]
    updated: int
    stale: int
    unavailable: int


def validate_quote(quote: ProviderQuote, instrument: ProviderInstrument, now: datetime) -> ProviderQuote:
    """Return a quote only when it exactly matches a valid requested instrument."""
    try:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be aware")
        if not isinstance(instrument.market, Market) or not isinstance(instrument.currency, Currency):
            raise ValueError("instrument enum invalid")
        if not isinstance(instrument.asset_type, AssetType):
            raise ValueError("instrument type invalid")
        if canonical_symbol(instrument.canonical_symbol, instrument.market) != instrument.canonical_symbol:
            raise ValueError("instrument symbol invalid")
        expected_currency = {
            Market.CN_SH: Currency.CNY,
            Market.CN_SZ: Currency.CNY,
            Market.CN_BJ: Currency.CNY,
            Market.HK: Currency.HKD,
            Market.US: Currency.USD,
        }[instrument.market]
        if instrument.currency is not expected_currency:
            raise ValueError("instrument currency invalid")
        if (
            quote.canonical_symbol != instrument.canonical_symbol
            or quote.provider_symbol != instrument.provider_symbol
            or not quote.provider
        ):
            raise ValueError("quote identity invalid")
        if quote.currency is not instrument.currency:
            raise ProviderFailure(ProviderErrorCode.CURRENCY_MISMATCH)
        if not isinstance(quote.price, Decimal) or parse_price(quote.price) != quote.price:
            raise ValueError("quote price invalid")
        if not isinstance(quote.as_of, datetime) or quote.as_of.tzinfo is None or quote.as_of.utcoffset() is None:
            raise ValueError("quote timestamp invalid")
        if quote.as_of > now + timedelta(minutes=5):
            raise ValueError("quote timestamp future")
    except ProviderFailure:
        raise
    except (DomainValidationError, TypeError, ValueError):
        raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
    return quote
