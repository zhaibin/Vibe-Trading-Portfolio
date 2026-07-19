import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from vibe_portfolio.market_data.eastmoney import EastmoneySearchProvider
from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market

FIXTURE = Path(__file__).parent / "fixtures" / "eastmoney_search.json"


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


async def test_eastmoney_malformed_payload_fails_closed() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"QuotationCodeTable": {"Data": {}}}))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        assert await EastmoneySearchProvider(http).search("demo", limit=5) == []


@pytest.mark.parametrize(("query", "limit"), [("x" * 81, 5), ("demo", 26)])
async def test_eastmoney_rejects_unbounded_requests_before_io(query: str, limit: int) -> None:
    transport = httpx.MockTransport(lambda _: pytest.fail("unexpected provider I/O"))
    async with BoundedProviderHttp(allowed_hosts={"searchapi.eastmoney.com"}, transport=transport) as http:
        with pytest.raises(ValueError):
            await EastmoneySearchProvider(http).search(query, limit=limit)
