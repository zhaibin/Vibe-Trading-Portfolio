"""Bounded Yahoo instrument-search adapter."""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import InstrumentCandidate, ProviderInstrument, ProviderQuote, ProviderSymbol
from vibe_portfolio.portfolio.domain import AssetType, Currency, DomainValidationError, Market, canonical_symbol

_SEARCH_ENDPOINT = "https://query2.finance.yahoo.com/v1/finance/search"
_US_EXCHANGES = frozenset({"ASE", "BTS", "NCM", "NGM", "NMS", "NYQ", "PCX"})
_ASSET_TYPES = {"EQUITY": AssetType.EQUITY, "ETF": AssetType.ETF}


def _validate_request(query: object, limit: object) -> tuple[str, int]:
    if not isinstance(query, str) or not 1 <= len(query) <= 80:
        raise ValueError("query must contain 1..80 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise ValueError("limit must be in 1..25")
    return query, limit


def _text(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _candidate(value: object) -> InstrumentCandidate | None:
    if not isinstance(value, Mapping):
        return None
    provider_symbol = _text(value, "symbol")
    name = _text(value, "shortname") or _text(value, "longname")
    quote_type = _text(value, "quoteType")
    exchange = _text(value, "exchange")
    currency_value = _text(value, "currency")
    if None in {provider_symbol, name, quote_type, exchange, currency_value}:
        return None
    assert provider_symbol is not None and name is not None and quote_type is not None
    assert exchange is not None and currency_value is not None
    asset_type = _ASSET_TYPES.get(quote_type.upper())
    if asset_type is None:
        return None
    if exchange.upper() == "HKG" and currency_value.upper() == Currency.HKD:
        market, currency = Market.HK, Currency.HKD
        code = provider_symbol.upper().removesuffix(".HK")
    elif exchange.upper() in _US_EXCHANGES and currency_value.upper() == Currency.USD:
        market, currency = Market.US, Currency.USD
        code = provider_symbol.upper().removesuffix(".US")
    else:
        return None
    try:
        symbol = canonical_symbol(code, market)
    except DomainValidationError:
        return None
    return InstrumentCandidate(
        canonical_symbol=symbol,
        name=name,
        market=market,
        currency=currency,
        asset_type=asset_type,
        provider_symbols=(ProviderSymbol("yahoo", provider_symbol),),
    )


class YahooSearchProvider:
    """Translate the reviewed Yahoo search payload to canonical candidates."""

    name = "yahoo"

    def __init__(self, http: BoundedProviderHttp) -> None:
        self._http = http

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        query, limit = _validate_request(query, limit)
        parameters = {"q": query, "quotesCount": limit, "newsCount": 0}
        payload = await self._http.get_json(f"{_SEARCH_ENDPOINT}?{urlencode(parameters)}")
        if not isinstance(payload, Mapping):
            return []
        quotes = payload.get("quotes")
        if not isinstance(quotes, list):
            return []
        return [candidate for value in quotes if (candidate := _candidate(value)) is not None][:limit]

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        return []
