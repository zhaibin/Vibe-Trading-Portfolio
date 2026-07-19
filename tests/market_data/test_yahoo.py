import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

import vibe_portfolio.market_data.yahoo as yahoo_module
from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import ProviderErrorCode, ProviderFailure, ProviderInstrument
from vibe_portfolio.market_data.yahoo import YahooSearchProvider
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_search.json"
QUOTE_FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_chart.json"


async def test_yahoo_normalizes_bare_us_and_four_digit_hk_and_filters_types() -> None:
    payload = json.loads(FIXTURE.read_text())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    async with BoundedProviderHttp(
        allowed_hosts={"query2.finance.yahoo.com"}, transport=httpx.MockTransport(handler)
    ) as http:
        candidates = await YahooSearchProvider(http).search("demo & labs", limit=7)

    assert [(item.canonical_symbol, item.market, item.currency, item.asset_type) for item in candidates] == [
        ("DEMO.US", Market.US, Currency.USD, AssetType.EQUITY),
        ("00999.HK", Market.HK, Currency.HKD, AssetType.EQUITY),
        ("ETFDEMO.US", Market.US, Currency.USD, AssetType.ETF),
    ]
    assert candidates[1].provider_symbols[0].symbol == "0999.HK"
    assert requests[0].url.host == "query2.finance.yahoo.com"
    assert requests[0].url.path == "/v1/finance/search"
    query = parse_qs(requests[0].url.query.decode())
    assert query["q"] == ["demo & labs"]
    assert query["quotesCount"] == ["7"]
    assert query["newsCount"] == ["0"]


@pytest.mark.parametrize("payload", [None, [], {}, {"quotes": {}}])
async def test_yahoo_wrong_envelope_is_a_sanitized_provider_failure(payload: object) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await YahooSearchProvider(http).search("demo", limit=5)
    assert raised.value.code is ProviderErrorCode.SEARCH_RESPONSE_INVALID
    assert raised.value.__cause__ is None


async def test_yahoo_valid_empty_envelope_is_an_empty_success() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"quotes": []}))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        assert await YahooSearchProvider(http).search("missing", limit=5) == []


@pytest.mark.parametrize(
    "row",
    [
        {"symbol": "0999", "shortname": "Bare HK", "quoteType": "EQUITY", "exchange": "HKG", "currency": "HKD"},
        {"symbol": "0999.HK", "shortname": "Foreign HK", "quoteType": "EQUITY", "exchange": "NMS", "currency": "USD"},
        {"symbol": "DEMO.SS", "shortname": "Foreign SH", "quoteType": "EQUITY", "exchange": "NMS", "currency": "USD"},
        {"symbol": "DEMO.SZ", "shortname": "Foreign SZ", "quoteType": "ETF", "exchange": "PCX", "currency": "USD"},
        {"symbol": "0999", "shortname": "Numeric US", "quoteType": "EQUITY", "exchange": "NMS", "currency": "USD"},
        {
            "symbol": "0999.HK",
            "shortname": "Wrong Currency",
            "quoteType": "EQUITY",
            "exchange": "HKG",
            "currency": "USD",
        },
    ],
)
async def test_yahoo_rejects_non_fetchable_or_contradictory_provider_identity(row: dict[str, str]) -> None:
    payload = {"quotes": [row]}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        assert await YahooSearchProvider(http).search("demo", limit=5) == []


async def test_yahoo_preserves_fetchable_us_dot_class_symbol() -> None:
    row = {
        "symbol": "DEMO.A",
        "shortname": "Fictional Dot Class",
        "quoteType": "EQUITY",
        "exchange": "NYQ",
        "currency": "USD",
    }
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"quotes": [row]}))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        results = await YahooSearchProvider(http).search("demo", limit=5)
    assert results[0].canonical_symbol == "DEMO.A.US"
    assert results[0].provider_symbols[0].symbol == "DEMO.A"


async def test_yahoo_drops_only_a_bounded_number_of_malformed_items() -> None:
    valid = json.loads(FIXTURE.read_text())["quotes"][0]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"quotes": [None, valid]}))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        results = await YahooSearchProvider(http).search("demo", limit=5)
    assert [item.canonical_symbol for item in results] == ["DEMO.US"]

    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"quotes": [None] * 6 + [valid]}))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await YahooSearchProvider(http).search("demo", limit=10)
    assert raised.value.code is ProviderErrorCode.SEARCH_RESPONSE_INVALID


async def test_yahoo_rejects_oversized_result_before_processing_items(monkeypatch: pytest.MonkeyPatch) -> None:
    processed = 0

    def trap(_: object) -> None:
        nonlocal processed
        processed += 1
        pytest.fail("oversized response item was processed")

    monkeypatch.setattr(yahoo_module, "_candidate", trap)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"quotes": [{}] * 26}))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await YahooSearchProvider(http).search("demo", limit=25)
    assert raised.value.code is ProviderErrorCode.RESPONSE_TOO_LARGE
    assert processed == 0


@pytest.mark.parametrize(("query", "limit"), [("x" * 81, 5), ("demo", 26)])
async def test_yahoo_rejects_unbounded_requests_before_io(query: str, limit: int) -> None:
    transport = httpx.MockTransport(lambda _: pytest.fail("unexpected provider I/O"))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        with pytest.raises(ValueError):
            await YahooSearchProvider(http).search(query, limit=limit)


async def test_yahoo_fetches_exact_decimal_currency_and_provider_timestamp() -> None:
    payload = json.loads(QUOTE_FIXTURE.read_text())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    instrument = ProviderInstrument(
        canonical_symbol="DEMO.US",
        provider_symbol="DEMO",
        market=Market.US,
        currency=Currency.USD,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(
        allowed_hosts={"query1.finance.yahoo.com"}, transport=httpx.MockTransport(handler)
    ) as http:
        quotes = await YahooSearchProvider(http).fetch_quotes([instrument])

    meta = payload["chart"]["result"][0]["meta"]
    assert quotes[0].price == Decimal("123.456789")
    assert quotes[0].as_of == datetime.fromtimestamp(meta["regularMarketTime"], UTC)
    assert quotes[0].currency is Currency.USD
    assert quotes[0].provider == "yahoo"
    assert requests[0].url.path == "/v8/finance/chart/DEMO"
    assert parse_qs(requests[0].url.query.decode()) == {"interval": ["1m"], "range": ["1d"]}


async def test_yahoo_quote_rejects_currency_mismatch() -> None:
    payload = json.loads(QUOTE_FIXTURE.read_text())
    payload["chart"]["result"][0]["meta"]["currency"] = "HKD"
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json=payload, headers={"content-type": "application/json"})
    )
    instrument = ProviderInstrument(
        canonical_symbol="DEMO.US",
        provider_symbol="DEMO",
        market=Market.US,
        currency=Currency.USD,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(allowed_hosts={"query1.finance.yahoo.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await YahooSearchProvider(http).fetch_quotes([instrument])
    assert raised.value.code is ProviderErrorCode.CURRENCY_MISMATCH


@pytest.mark.parametrize(
    ("field", "value"), [("regularMarketPrice", 123.4567891), ("regularMarketTime", 4_102_444_800)]
)
async def test_yahoo_quote_rejects_overprecision_or_far_future_values(field: str, value: object) -> None:
    payload = json.loads(QUOTE_FIXTURE.read_text())
    payload["chart"]["result"][0]["meta"][field] = value
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json=payload, headers={"content-type": "application/json"})
    )
    instrument = ProviderInstrument(
        canonical_symbol="DEMO.US",
        provider_symbol="DEMO",
        market=Market.US,
        currency=Currency.USD,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(allowed_hosts={"query1.finance.yahoo.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await YahooSearchProvider(http).fetch_quotes([instrument])
    assert raised.value.code is ProviderErrorCode.RESPONSE_INVALID
