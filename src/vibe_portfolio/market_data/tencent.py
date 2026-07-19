"""Bounded Tencent quote adapter for reviewed mainland symbols."""

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import NoReturn
from zoneinfo import ZoneInfo

from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import (
    InstrumentCandidate,
    ProviderErrorCode,
    ProviderFailure,
    ProviderInstrument,
    ProviderQuote,
    validate_quote,
)
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market

_QUOTE_ENDPOINT = "https://qt.gtimg.cn/q="
_TENCENT_SYMBOL = re.compile(r"(?:sh[56]\d{5}|sz[013]\d{5})")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _invalid_quote() -> NoReturn:
    raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None


class TencentQuoteProvider:
    name = "tencent"

    def __init__(self, http: BoundedProviderHttp) -> None:
        self._http = http

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        return []

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        quotes: list[ProviderQuote] = []
        for instrument in instruments:
            if not _valid_instrument(instrument):
                _invalid_quote()
            payload = await self._http.get_text(f"{_QUOTE_ENDPOINT}{instrument.provider_symbol}", encoding="gb18030")
            quote = _parse_quote(payload, instrument)
            if quote is not None:
                quotes.append(quote)
        return quotes


def _valid_instrument(instrument: ProviderInstrument) -> bool:
    if instrument.currency is not Currency.CNY or _TENCENT_SYMBOL.fullmatch(instrument.provider_symbol) is None:
        return False
    prefix, code = instrument.provider_symbol[:2], instrument.provider_symbol[2:]
    expected = {Market.CN_SH: "sh", Market.CN_SZ: "sz"}.get(instrument.market)
    expected_code = {
        (Market.CN_SH, AssetType.EQUITY): r"6\d{5}",
        (Market.CN_SH, AssetType.ETF): r"5\d{5}",
        (Market.CN_SZ, AssetType.EQUITY): r"[03]\d{5}",
        (Market.CN_SZ, AssetType.ETF): r"1\d{5}",
    }.get((instrument.market, instrument.asset_type))
    return (
        prefix == expected
        and expected_code is not None
        and re.fullmatch(expected_code, code) is not None
        and instrument.canonical_symbol == f"{code}.{'SH' if prefix == 'sh' else 'SZ'}"
    )


def _parse_quote(payload: str, instrument: ProviderInstrument) -> ProviderQuote | None:
    match = re.fullmatch(rf'v_{re.escape(instrument.provider_symbol)}="([^"]*)";\s*', payload)
    if match is None:
        _invalid_quote()
    body = match.group(1)
    if not body:
        return None
    fields = body.split("~")
    if len(fields) <= 30 or fields[2] != instrument.provider_symbol[2:]:
        _invalid_quote()
    try:
        price = Decimal(fields[3])
        if not price.is_finite() or price <= 0:
            _invalid_quote()
        local_time = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=_SHANGHAI)
        as_of = local_time.astimezone(UTC)
    except ProviderFailure:
        raise
    except (InvalidOperation, OverflowError, ValueError):
        _invalid_quote()
    quote = ProviderQuote(
        canonical_symbol=instrument.canonical_symbol,
        provider_symbol=instrument.provider_symbol,
        price=price,
        currency=Currency.CNY,
        as_of=as_of,
        provider="tencent",
    )
    return validate_quote(quote, instrument, datetime.now(UTC))
