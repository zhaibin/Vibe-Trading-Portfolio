"""Bounded Eastmoney instrument-search adapter."""

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

_SEARCH_ENDPOINT = "https://searchapi.eastmoney.com/api/suggest/get"
_US_MARKET_IDS = frozenset({"105", "106", "107"})
_REVIEWED_MARKET_IDS = frozenset({"0", "1", "105", "106", "107", "116"})
_MAX_SEARCH_ITEMS = 25
_MAX_MALFORMED_ITEMS = 5
_US_SYMBOL = re.compile(r"(?=.{1,15}\Z)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*(?:\.[A-Z0-9])?")


class _MalformedItem(ValueError):
    pass


def _invalid_response() -> NoReturn:
    raise ProviderFailure(ProviderErrorCode.SEARCH_RESPONSE_INVALID) from None


def _text(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if type(value) is not str or not value.strip():
        raise _MalformedItem()
    return value.strip()


def _identity(code: str, market_id: str, type_name: str) -> tuple[Market, Currency, AssetType] | None:
    if market_id not in _REVIEWED_MARKET_IDS:
        return None
    if type_name == "沪A":
        if market_id != "1" or re.fullmatch(r"6\d{5}", code) is None:
            raise _MalformedItem()
        return Market.CN_SH, Currency.CNY, AssetType.EQUITY
    if type_name == "深A":
        if market_id != "0" or re.fullmatch(r"[03]\d{5}", code) is None:
            raise _MalformedItem()
        return Market.CN_SZ, Currency.CNY, AssetType.EQUITY
    if type_name == "北证A股":
        if market_id != "0" or re.fullmatch(r"(?:[48]\d{5}|92\d{4})", code) is None:
            raise _MalformedItem()
        return Market.CN_BJ, Currency.CNY, AssetType.EQUITY
    if type_name == "港股":
        if market_id != "116" or re.fullmatch(r"\d{1,5}", code) is None:
            raise _MalformedItem()
        return Market.HK, Currency.HKD, AssetType.EQUITY
    if type_name == "美股":
        if market_id not in _US_MARKET_IDS or _US_SYMBOL.fullmatch(code.upper()) is None:
            raise _MalformedItem()
        return Market.US, Currency.USD, AssetType.EQUITY
    if type_name != "ETF":
        return None
    if market_id == "1" and re.fullmatch(r"5\d{5}", code) is not None:
        return Market.CN_SH, Currency.CNY, AssetType.ETF
    if market_id == "0" and re.fullmatch(r"1\d{5}", code) is not None:
        return Market.CN_SZ, Currency.CNY, AssetType.ETF
    if market_id == "116" and re.fullmatch(r"\d{1,5}", code) is not None:
        return Market.HK, Currency.HKD, AssetType.ETF
    if market_id in _US_MARKET_IDS and _US_SYMBOL.fullmatch(code.upper()) is not None:
        return Market.US, Currency.USD, AssetType.ETF
    raise _MalformedItem()


def _validate_request(query: object, limit: object) -> tuple[str, int]:
    if not isinstance(query, str) or not 1 <= len(query) <= 80:
        raise ValueError("query must contain 1..80 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise ValueError("limit must be in 1..25")
    return query, limit


def _candidate(value: object) -> InstrumentCandidate | None:
    if type(value) is not dict:
        raise _MalformedItem()
    code = _text(value, "Code")
    name = _text(value, "Name")
    market_id = _text(value, "MktNum")
    quote_id = _text(value, "QuoteID")
    type_name = _text(value, "SecurityTypeName")
    identity = _identity(code, market_id, type_name)
    if identity is None:
        return None
    quote_market, separator, quote_code = quote_id.partition(".")
    if separator != "." or quote_market != market_id:
        raise _MalformedItem()
    market, currency, asset_type = identity
    try:
        symbol = canonical_symbol(code, market)
        if canonical_symbol(quote_code, market) != symbol:
            raise _MalformedItem()
    except DomainValidationError:
        raise _MalformedItem() from None
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
        try:
            payload = await self._http.get_json(url)
        except ProviderFailure as error:
            if error.code is ProviderErrorCode.RESPONSE_INVALID:
                _invalid_response()
            raise
        if type(payload) is not dict:
            _invalid_response()
        table = payload.get("QuotationCodeTable")
        if type(table) is not dict:
            _invalid_response()
        data = table.get("Data")
        if type(data) is not list:
            _invalid_response()
        if len(data) > _MAX_SEARCH_ITEMS:
            raise ProviderFailure(ProviderErrorCode.RESPONSE_TOO_LARGE) from None
        candidates: list[InstrumentCandidate] = []
        malformed = 0
        for value in data:
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
