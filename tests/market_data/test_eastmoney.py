import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

import vibe_portfolio.market_data.eastmoney as eastmoney_module
from vibe_portfolio.market_data.eastmoney import EastmoneySearchProvider
from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import ProviderErrorCode, ProviderFailure, ProviderInstrument
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market

FIXTURE = Path(__file__).parent / "fixtures" / "eastmoney_search.json"
QUOTE_FIXTURE = Path(__file__).parent / "fixtures" / "eastmoney_quote.json"


async def test_eastmoney_maps_only_reviewed_markets_and_equity_types() -> None:
    payload = json.loads(FIXTURE.read_text())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    async with BoundedProviderHttp(
        allowed_hosts={"searchapi.eastmoney.com"}, transport=httpx.MockTransport(handler)
    ) as http:
        candidates = await EastmoneySearchProvider(http).search("demo / 示例", limit=25)

    assert [(item.canonical_symbol, item.market) for item in candidates] == [
        ("699991.SH", Market.CN_SH),
        ("099991.SZ", Market.CN_SZ),
        ("499991.BJ", Market.CN_BJ),
        ("899991.BJ", Market.CN_BJ),
        ("929991.BJ", Market.CN_BJ),
        ("09999.HK", Market.HK),
        ("DEMO.US", Market.US),
        ("ALPHA.US", Market.US),
        ("BETA.US", Market.US),
    ]
    assert candidates[5].currency is Currency.HKD
    assert candidates[8].asset_type is AssetType.ETF
    assert all(item.provider_symbols[0].provider == "eastmoney" for item in candidates)
    assert requests[0].url.host == "searchapi.eastmoney.com"
    assert requests[0].url.path == "/api/suggest/get"
    assert parse_qs(requests[0].url.query.decode())["input"] == ["demo / 示例"]
    assert parse_qs(requests[0].url.query.decode())["type"] == ["14"]
    assert parse_qs(requests[0].url.query.decode())["count"] == ["25"]


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"QuotationCodeTable": []}, {"QuotationCodeTable": {}}, {"QuotationCodeTable": {"Data": {}}}],
)
async def test_eastmoney_wrong_envelope_is_a_sanitized_provider_failure(payload: object) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await EastmoneySearchProvider(http).search("demo", limit=5)
    assert raised.value.code is ProviderErrorCode.SEARCH_RESPONSE_INVALID
    assert raised.value.__cause__ is None


async def test_eastmoney_valid_empty_envelope_is_an_empty_success() -> None:
    payload = {"QuotationCodeTable": {"Data": []}}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        assert await EastmoneySearchProvider(http).search("missing", limit=5) == []


async def test_eastmoney_drops_a_bounded_number_of_malformed_items() -> None:
    valid = json.loads(FIXTURE.read_text())["QuotationCodeTable"]["Data"][0]
    payload = {"QuotationCodeTable": {"Data": [None, valid]}}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        results = await EastmoneySearchProvider(http).search("demo", limit=5)
    assert [item.canonical_symbol for item in results] == ["699991.SH"]

    payload = {"QuotationCodeTable": {"Data": [None] * 6 + [valid]}}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await EastmoneySearchProvider(http).search("demo", limit=10)
    assert raised.value.code is ProviderErrorCode.SEARCH_RESPONSE_INVALID


@pytest.mark.parametrize(
    "row",
    [
        {"Code": "499991", "Name": "Contradictory SZ", "MktNum": "0", "QuoteID": "0.499991", "SecurityTypeName": "深A"},
        {
            "Code": "099991",
            "Name": "Contradictory BJ",
            "MktNum": "0",
            "QuoteID": "0.099991",
            "SecurityTypeName": "北证A股",
        },
        {"Code": "099991", "Name": "Contradictory SH", "MktNum": "1", "QuoteID": "1.099991", "SecurityTypeName": "沪A"},
        {
            "Code": "9999",
            "Name": "Contradictory HK",
            "MktNum": "116",
            "QuoteID": "116.9999",
            "SecurityTypeName": "美股",
        },
        {
            "Code": "DEMO",
            "Name": "Contradictory US",
            "MktNum": "105",
            "QuoteID": "105.DEMO",
            "SecurityTypeName": "港股",
        },
        {
            "Code": "699991",
            "Name": "Contradictory ETF",
            "MktNum": "1",
            "QuoteID": "1.699991",
            "SecurityTypeName": "ETF",
        },
    ],
)
async def test_eastmoney_rejects_contradictory_market_type_and_code_evidence(row: dict[str, str]) -> None:
    payload = {"QuotationCodeTable": {"Data": [row]}}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        assert await EastmoneySearchProvider(http).search("demo", limit=5) == []


async def test_eastmoney_rejects_oversized_result_before_processing_items(monkeypatch: pytest.MonkeyPatch) -> None:
    processed = 0

    def trap(_: object) -> None:
        nonlocal processed
        processed += 1
        pytest.fail("oversized response item was processed")

    monkeypatch.setattr(eastmoney_module, "_candidate", trap)
    payload = {"QuotationCodeTable": {"Data": [{}] * 26}}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await EastmoneySearchProvider(http).search("demo", limit=25)
    assert raised.value.code is ProviderErrorCode.RESPONSE_TOO_LARGE
    assert processed == 0


@pytest.mark.parametrize(("query", "limit"), [("x" * 81, 5), ("demo", 26)])
async def test_eastmoney_rejects_unbounded_requests_before_io(query: str, limit: int) -> None:
    transport = httpx.MockTransport(lambda _: pytest.fail("unexpected provider I/O"))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        with pytest.raises(ValueError):
            await EastmoneySearchProvider(http).search(query, limit=limit)


async def test_eastmoney_fetches_exact_decimal_and_provider_timestamp() -> None:
    payload = json.loads(QUOTE_FIXTURE.read_text())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    instrument = ProviderInstrument(
        canonical_symbol="600000.SH",
        provider_symbol="1.600000",
        market=Market.CN_SH,
        currency=Currency.CNY,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(
        allowed_hosts={"push2.eastmoney.com"}, transport=httpx.MockTransport(handler)
    ) as http:
        quotes = await EastmoneySearchProvider(http).fetch_quotes([instrument])

    assert quotes[0].price == Decimal("12.3456")
    assert quotes[0].as_of == datetime.fromtimestamp(payload["data"]["f86"], UTC)
    assert quotes[0].currency is Currency.CNY
    assert quotes[0].provider == "eastmoney"
    assert requests[0].url.path == "/api/qt/stock/get"
    assert parse_qs(requests[0].url.query.decode()) == {
        "secid": ["1.600000"],
        "fields": ["f43,f57,f58,f59,f86"],
    }


@pytest.mark.parametrize("data", [None, {}, {"f43": "NaN", "f57": "600000", "f58": "Demo", "f59": 2, "f86": 1}])
async def test_eastmoney_quote_rejects_malformed_payloads(data: object) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": data}, headers={"content-type": "application/json"})
    )
    instrument = ProviderInstrument(
        canonical_symbol="600000.SH",
        provider_symbol="1.600000",
        market=Market.CN_SH,
        currency=Currency.CNY,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(allowed_hosts={"push2.eastmoney.com"}, transport=transport) as http:
        if data is None:
            assert await EastmoneySearchProvider(http).fetch_quotes([instrument]) == []
        else:
            with pytest.raises(ProviderFailure) as raised:
                await EastmoneySearchProvider(http).fetch_quotes([instrument])
            assert raised.value.code is ProviderErrorCode.RESPONSE_INVALID


async def test_eastmoney_quote_rejects_far_future_provider_timestamp() -> None:
    payload = json.loads(QUOTE_FIXTURE.read_text())
    payload["data"]["f86"] = 4_102_444_800
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json=payload, headers={"content-type": "application/json"})
    )
    instrument = ProviderInstrument(
        canonical_symbol="600000.SH",
        provider_symbol="1.600000",
        market=Market.CN_SH,
        currency=Currency.CNY,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(allowed_hosts={"push2.eastmoney.com"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await EastmoneySearchProvider(http).fetch_quotes([instrument])
    assert raised.value.code is ProviderErrorCode.RESPONSE_INVALID
