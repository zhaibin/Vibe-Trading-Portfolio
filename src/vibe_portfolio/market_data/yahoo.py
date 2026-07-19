"""Bounded Yahoo instrument-search adapter."""

import re
from collections.abc import Sequence
from typing import NoReturn
from urllib.parse import urlencode

from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import (
    InstrumentCandidate,
    ProviderErrorCode,
    ProviderFailure,
    ProviderInstrument,
    ProviderQuote,
    ProviderSymbol,
)
from vibe_portfolio.portfolio.domain import AssetType, Currency, DomainValidationError, Market, canonical_symbol

_SEARCH_ENDPOINT = "https://query2.finance.yahoo.com/v1/finance/search"
_US_EXCHANGES = frozenset({"ASE", "BTS", "NCM", "NGM", "NMS", "NYQ", "PCX"})
_ASSET_TYPES = {"EQUITY": AssetType.EQUITY, "ETF": AssetType.ETF}
_MAX_SEARCH_ITEMS = 25
_MAX_MALFORMED_ITEMS = 5
_HK_SYMBOL = re.compile(r"\d{1,5}\.HK")
_US_SYMBOL = re.compile(r"(?=.{1,15}\Z)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*(?:\.[A-Z0-9])?")


class _MalformedItem(ValueError):
    pass


def _invalid_response() -> NoReturn:
    raise ProviderFailure(ProviderErrorCode.SEARCH_RESPONSE_INVALID) from None


def _validate_request(query: object, limit: object) -> tuple[str, int]:
    if not isinstance(query, str) or not 1 <= len(query) <= 80:
        raise ValueError("query must contain 1..80 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise ValueError("limit must be in 1..25")
    return query, limit


def _text(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        raise _MalformedItem()
    return value.strip()


def _candidate(value: object) -> InstrumentCandidate | None:
    if type(value) is not dict:
        raise _MalformedItem()
    provider_symbol = _text(value, "symbol")
    name = _text(value, "shortname") or _text(value, "longname")
    quote_type = _text(value, "quoteType")
    exchange = _text(value, "exchange")
    currency_value = _text(value, "currency")
    if None in {provider_symbol, name, quote_type, exchange, currency_value}:
        raise _MalformedItem()
    assert provider_symbol is not None and name is not None and quote_type is not None
    assert exchange is not None and currency_value is not None
    asset_type = _ASSET_TYPES.get(quote_type.upper())
    if asset_type is None:
        return None
    normalized_symbol = provider_symbol.upper()
    normalized_exchange = exchange.upper()
    normalized_currency = currency_value.upper()
    if normalized_exchange == "HKG" and normalized_currency == Currency.HKD:
        if _HK_SYMBOL.fullmatch(normalized_symbol) is None:
            raise _MalformedItem()
        market, currency = Market.HK, Currency.HKD
        code = normalized_symbol.removesuffix(".HK")
    elif normalized_exchange in _US_EXCHANGES and normalized_currency == Currency.USD:
        if _US_SYMBOL.fullmatch(normalized_symbol) is None:
            raise _MalformedItem()
        market, currency = Market.US, Currency.USD
        code = normalized_symbol
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
        provider_symbols=(ProviderSymbol("yahoo", normalized_symbol),),
    )


class YahooSearchProvider:
    """Translate the reviewed Yahoo search payload to canonical candidates."""

    name = "yahoo"

    def __init__(self, http: BoundedProviderHttp) -> None:
        self._http = http

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        query, limit = _validate_request(query, limit)
        parameters = {"q": query, "quotesCount": limit, "newsCount": 0}
        try:
            payload = await self._http.get_json(f"{_SEARCH_ENDPOINT}?{urlencode(parameters)}")
        except ProviderFailure as error:
            if error.code is ProviderErrorCode.RESPONSE_INVALID:
                _invalid_response()
            raise
        if type(payload) is not dict:
            _invalid_response()
        quotes = payload.get("quotes")
        if type(quotes) is not list:
            _invalid_response()
        if len(quotes) > _MAX_SEARCH_ITEMS:
            raise ProviderFailure(ProviderErrorCode.RESPONSE_TOO_LARGE) from None
        candidates: list[InstrumentCandidate] = []
        malformed = 0
        for value in quotes:
            try:
                candidate = _candidate(value)
            except _MalformedItem:
                malformed += 1
                if malformed > _MAX_MALFORMED_ITEMS:
                    _invalid_response()
                continue
            if candidate is not None:
                candidates.append(candidate)
        return candidates[:limit]

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        return []
