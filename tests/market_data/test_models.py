from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from vibe_portfolio.market_data.models import (
    InstrumentCandidate,
    ProviderErrorCode,
    ProviderFailure,
    ProviderInstrument,
    ProviderQuote,
    ProviderSymbol,
    RefreshResult,
    RefreshScope,
    validate_quote,
)
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def instrument(**overrides: object) -> ProviderInstrument:
    values: dict[str, object] = {
        "canonical_symbol": "00700.HK",
        "provider_symbol": "0700.HK",
        "market": Market.HK,
        "currency": Currency.HKD,
        "asset_type": AssetType.EQUITY,
    }
    values.update(overrides)
    return ProviderInstrument(**values)  # type: ignore[arg-type]


def quote(**overrides: object) -> ProviderQuote:
    values: dict[str, object] = {
        "canonical_symbol": "00700.HK",
        "provider_symbol": "0700.HK",
        "price": Decimal("321.123456"),
        "currency": Currency.HKD,
        "as_of": NOW,
        "provider": "yahoo",
    }
    values.update(overrides)
    return ProviderQuote(**values)  # type: ignore[arg-type]


def test_provider_dtos_are_immutable_and_candidate_sources_preserve_order() -> None:
    candidate = InstrumentCandidate(
        canonical_symbol="00700.HK",
        name="Example Holdings",
        market=Market.HK,
        currency=Currency.HKD,
        asset_type=AssetType.EQUITY,
        provider_symbols=(ProviderSymbol("eastmoney", "116.00700"), ProviderSymbol("yahoo", "0700.HK")),
    )

    assert candidate.sources == ("eastmoney", "yahoo")
    with pytest.raises(FrozenInstanceError):
        candidate.name = "changed"  # type: ignore[misc]


def test_refresh_scope_and_result_have_exact_public_values() -> None:
    run_id = UUID("11111111-1111-4111-8111-111111111111")

    assert RefreshScope.all() == RefreshScope(instrument_ids=None)
    assert RefreshResult(run_id, "partial", 2, 1, 3).status == "partial"


@pytest.mark.parametrize(
    "overrides",
    [
        {"canonical_symbol": "AAPL.US"},
        {"provider_symbol": "other"},
        {"price": Decimal("0")},
        {"price": Decimal("NaN")},
        {"price": Decimal("1.0000001")},
        {"as_of": datetime(2026, 7, 19, 12, 0)},
        {"as_of": NOW + timedelta(minutes=5, microseconds=1)},
        {"provider": ""},
    ],
)
def test_provider_quote_rejects_invalid_identity_price_or_timestamp(overrides: dict[str, object]) -> None:
    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID") as raised:
        validate_quote(quote(**overrides), instrument(), now=NOW)

    assert raised.value.code is ProviderErrorCode.RESPONSE_INVALID
    assert raised.value.args == ("QUOTE_RESPONSE_INVALID",)


def test_provider_quote_rejects_currency_mismatch() -> None:
    with pytest.raises(ProviderFailure, match="QUOTE_CURRENCY_MISMATCH") as raised:
        validate_quote(quote(currency=Currency.USD), instrument(), now=NOW)

    assert raised.value.code is ProviderErrorCode.CURRENCY_MISMATCH


def test_provider_quote_accepts_exact_future_boundary_and_returns_same_value() -> None:
    value = quote(as_of=NOW + timedelta(minutes=5))

    assert validate_quote(value, instrument(), now=NOW) is value


@pytest.mark.parametrize(
    "invalid_instrument",
    [
        instrument(canonical_symbol="700.HK"),
        instrument(currency=Currency.USD),
        instrument(asset_type="crypto"),
    ],
)
def test_provider_quote_rejects_malformed_instrument_contract(invalid_instrument: ProviderInstrument) -> None:
    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID"):
        validate_quote(quote(), invalid_instrument, now=NOW)
