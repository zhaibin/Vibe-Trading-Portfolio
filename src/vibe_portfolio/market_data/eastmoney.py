"""Bounded Eastmoney instrument-search adapter."""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import InstrumentCandidate, ProviderInstrument, ProviderQuote, ProviderSymbol
from vibe_portfolio.portfolio.domain import AssetType, Currency, DomainValidationError, Market, canonical_symbol

_SEARCH_ENDPOINT = "https://searchapi.eastmoney.com/api/suggest/get"
_US_MARKET_IDS = frozenset({"105", "106", "107"})
_EQUITY_TYPE_NAMES = frozenset({"沪A", "深A", "北证A股", "港股", "美股"})


def _text(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _market(code: str, market_id: str) -> tuple[Market, Currency] | None:
    if market_id == "1":
        return Market.CN_SH, Currency.CNY
    if market_id == "0":
        market = Market.CN_BJ if code.startswith(("4", "8", "92")) else Market.CN_SZ
        return market, Currency.CNY
    if market_id == "116":
        return Market.HK, Currency.HKD
    if market_id in _US_MARKET_IDS:
        return Market.US, Currency.USD
    return None


def _validate_request(query: object, limit: object) -> tuple[str, int]:
    if not isinstance(query, str) or not 1 <= len(query) <= 80:
        raise ValueError("query must contain 1..80 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise ValueError("limit must be in 1..25")
    return query, limit


def _candidate(value: object) -> InstrumentCandidate | None:
    if not isinstance(value, Mapping):
        return None
    code = _text(value, "Code")
    name = _text(value, "Name")
    market_id = _text(value, "MktNum")
    quote_id = _text(value, "QuoteID")
    type_name = _text(value, "SecurityTypeName")
    if None in {code, name, market_id, quote_id, type_name}:
        return None
    assert code is not None and name is not None and market_id is not None and quote_id is not None
    assert type_name is not None
    identity = _market(code, market_id)
    quote_market, separator, quote_code = quote_id.partition(".")
    if identity is None or separator != "." or quote_market != market_id:
        return None
    if type_name == "ETF":
        asset_type = AssetType.ETF
    elif type_name in _EQUITY_TYPE_NAMES:
        asset_type = AssetType.EQUITY
    else:
        return None
    market, currency = identity
    try:
        symbol = canonical_symbol(code, market)
        if canonical_symbol(quote_code, market) != symbol:
            return None
    except DomainValidationError:
        return None
    return InstrumentCandidate(
        canonical_symbol=symbol,
        name=name,
        market=market,
        currency=currency,
        asset_type=asset_type,
        provider_symbols=(ProviderSymbol("eastmoney", quote_id),),
    )


class EastmoneySearchProvider:
    """Translate the reviewed Eastmoney suggestion payload to canonical candidates."""

    name = "eastmoney"

    def __init__(self, http: BoundedProviderHttp) -> None:
        self._http = http

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        query, limit = _validate_request(query, limit)
        url = f"{_SEARCH_ENDPOINT}?{urlencode({'input': query, 'type': 14, 'count': limit})}"
        payload = await self._http.get_json(url)
        if not isinstance(payload, Mapping):
            return []
        table = payload.get("QuotationCodeTable")
        if not isinstance(table, Mapping):
            return []
        data = table.get("Data")
        if not isinstance(data, list):
            return []
        return [candidate for value in data if (candidate := _candidate(value)) is not None][:limit]

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        return []
