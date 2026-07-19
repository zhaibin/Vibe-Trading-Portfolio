import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.yahoo import YahooSearchProvider
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_search.json"


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


async def test_yahoo_malformed_payload_fails_closed() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"quotes": {}}))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        assert await YahooSearchProvider(http).search("demo", limit=5) == []


@pytest.mark.parametrize(("query", "limit"), [("x" * 81, 5), ("demo", 26)])
async def test_yahoo_rejects_unbounded_requests_before_io(query: str, limit: int) -> None:
    transport = httpx.MockTransport(lambda _: pytest.fail("unexpected provider I/O"))
    async with BoundedProviderHttp(allowed_hosts={"query2.finance.yahoo.com"}, transport=transport) as http:
        with pytest.raises(ValueError):
            await YahooSearchProvider(http).search(query, limit=limit)
