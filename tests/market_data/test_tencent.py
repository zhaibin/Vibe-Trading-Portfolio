from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import ProviderErrorCode, ProviderFailure, ProviderInstrument
from vibe_portfolio.market_data.tencent import TencentQuoteProvider
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market

FIXTURE = Path(__file__).parent / "fixtures" / "tencent_quote.txt"


async def test_tencent_decodes_gb18030_price_and_converts_shanghai_timestamp_to_utc() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=FIXTURE.read_bytes(), headers={"content-type": "text/plain"})

    instrument = ProviderInstrument(
        canonical_symbol="600000.SH",
        provider_symbol="sh600000",
        market=Market.CN_SH,
        currency=Currency.CNY,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(allowed_hosts={"qt.gtimg.cn"}, transport=httpx.MockTransport(handler)) as http:
        quotes = await TencentQuoteProvider(http).fetch_quotes([instrument])

    assert quotes[0].price == Decimal("12.3456")
    assert quotes[0].as_of == datetime(2026, 7, 19, 7, 0, tzinfo=UTC)
    assert quotes[0].provider == "tencent"
    assert requests[0].url == "https://qt.gtimg.cn/q=sh600000"


async def test_tencent_rejects_wrong_response_identity() -> None:
    payload = FIXTURE.read_text().replace("v_sh600000=", "v_sz000001=").encode("gb18030")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=payload))
    instrument = ProviderInstrument(
        canonical_symbol="600000.SH",
        provider_symbol="sh600000",
        market=Market.CN_SH,
        currency=Currency.CNY,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(allowed_hosts={"qt.gtimg.cn"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await TencentQuoteProvider(http).fetch_quotes([instrument])
    assert raised.value.code is ProviderErrorCode.RESPONSE_INVALID


@pytest.mark.parametrize(("old", "new"), [("12.3456", "12.3456789"), ("20260719150000", "21000101000000")])
async def test_tencent_rejects_overprecision_or_far_future_values(old: str, new: str) -> None:
    payload = FIXTURE.read_text().replace(old, new).encode("gb18030")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=payload))
    instrument = ProviderInstrument(
        canonical_symbol="600000.SH",
        provider_symbol="sh600000",
        market=Market.CN_SH,
        currency=Currency.CNY,
        asset_type=AssetType.EQUITY,
    )
    async with BoundedProviderHttp(allowed_hosts={"qt.gtimg.cn"}, transport=transport) as http:
        with pytest.raises(ProviderFailure) as raised:
            await TencentQuoteProvider(http).fetch_quotes([instrument])
    assert raised.value.code is ProviderErrorCode.RESPONSE_INVALID


async def test_tencent_accepts_reviewed_mainland_etf_identity() -> None:
    payload = (
        FIXTURE.read_text()
        .replace("sh600000", "sh510300")
        .replace("~600000~", "~510300~")
        .encode("gb18030")
    )
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=payload))
    instrument = ProviderInstrument(
        canonical_symbol="510300.SH",
        provider_symbol="sh510300",
        market=Market.CN_SH,
        currency=Currency.CNY,
        asset_type=AssetType.ETF,
    )
    async with BoundedProviderHttp(allowed_hosts={"qt.gtimg.cn"}, transport=transport) as http:
        quotes = await TencentQuoteProvider(http).fetch_quotes([instrument])
    assert quotes[0].canonical_symbol == "510300.SH"
