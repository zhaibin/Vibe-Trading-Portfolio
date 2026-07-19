from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from vibe_portfolio.portfolio.domain import (
    DomainValidationError,
    Market,
    QuoteState,
    canonical_symbol,
    parse_money,
    parse_price,
    parse_quantity,
    quote_state,
)


@pytest.mark.parametrize(
    ("code", "market", "expected"),
    [
        ("600519", Market.CN_SH, "600519.SH"),
        ("920001", Market.CN_BJ, "920001.BJ"),
        ("700", Market.HK, "00700.HK"),
        ("aapl", Market.US, "AAPL.US"),
    ],
)
def test_canonical_symbol(code: str, market: Market, expected: str) -> None:
    assert canonical_symbol(code, market) == expected


@pytest.mark.parametrize(
    ("code", "market", "expected"),
    [
        ("700", "HK", "00700.HK"),
        ("aapl", "US", "AAPL.US"),
    ],
)
def test_canonical_symbol_coerces_supported_raw_market_values(
    code: str, market: str, expected: str
) -> None:
    assert canonical_symbol(code, market) == expected


@pytest.mark.parametrize("market", ["not_a_market", "hk", "", None])
def test_canonical_symbol_rejects_unsupported_raw_market_values(market: object) -> None:
    with pytest.raises(DomainValidationError, match="market_invalid"):
        canonical_symbol("700", market)


@pytest.mark.parametrize(
    ("code", "market"),
    [
        ("60051", Market.CN_SH),
        ("123456", Market.HK),
        ("BRK/B", Market.US),
        ("", Market.US),
    ],
)
def test_canonical_symbol_rejects_malformed_codes(code: str, market: Market) -> None:
    with pytest.raises(DomainValidationError, match="symbol_invalid"):
        canonical_symbol(code, market)


def test_quantity_must_be_positive() -> None:
    for value in ("0", "-0.00000001"):
        with pytest.raises(DomainValidationError, match="quantity_range"):
            parse_quantity(value)


def test_money_may_be_zero_but_not_negative() -> None:
    assert parse_money("0") == Decimal("0")
    with pytest.raises(DomainValidationError, match="money_range"):
        parse_money("-0.000001")


@pytest.mark.parametrize("parser", [parse_quantity, parse_money, parse_price])
def test_exact_magnitude_ceiling_is_accepted(parser: Callable[[str | Decimal], Decimal]) -> None:
    assert parser("1000000000000") == Decimal("1000000000000")
    with pytest.raises(DomainValidationError, match="_range"):
        parser("1000000000000.000001")


def test_decimal_precision_is_rejected_not_rounded() -> None:
    with pytest.raises(DomainValidationError, match="quantity_precision"):
        parse_quantity("1.000000001")
    with pytest.raises(DomainValidationError, match="money_precision"):
        parse_money("10.0000001")


def test_quote_state_is_derived_from_latest_attempt() -> None:
    now = datetime(2026, 7, 19, 12, tzinfo=UTC)
    assert quote_state(now - timedelta(hours=1), latest_attempt_succeeded=True, now=now) is QuoteState.FRESH
    assert quote_state(now - timedelta(hours=1), latest_attempt_succeeded=False, now=now) is QuoteState.STALE
    assert quote_state(None, latest_attempt_succeeded=False, now=now) is QuoteState.UNAVAILABLE


def test_quote_state_treats_future_timestamp_as_fresh_when_attempt_succeeds() -> None:
    now = datetime(2026, 7, 19, 12, tzinfo=UTC)
    assert quote_state(now + timedelta(minutes=5), latest_attempt_succeeded=True, now=now) is QuoteState.FRESH
