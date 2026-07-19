"""Instrument-search orchestration and trusted candidate caching."""

import asyncio
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from vibe_portfolio.market_data.models import InstrumentCandidate, ProviderSymbol
from vibe_portfolio.market_data.protocol import MarketDataProvider
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import AssetType, Currency, DomainValidationError, Market, canonical_symbol
from vibe_portfolio.portfolio.repository import CandidateInput, PortfolioRepository

_WHITESPACE = re.compile(r"\s+")
_ALLOWED_PUNCTUATION = frozenset(" .-&/")
_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_PROVIDER_SYMBOL = re.compile(r"[A-Za-z0-9.^=-]{1,64}")
_US_PROVIDER_SYMBOL = re.compile(r"(?=.{1,15}\Z)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*(?:\.[A-Z0-9])?")
_HK_PROVIDER_SYMBOL = re.compile(r"\d{1,5}\.HK")
_MAX_PROVIDER_ITEMS = 25
_EXPECTED_CURRENCY = {
    Market.CN_SH: Currency.CNY,
    Market.CN_SZ: Currency.CNY,
    Market.CN_BJ: Currency.CNY,
    Market.HK: Currency.HKD,
    Market.US: Currency.USD,
}
_PROVIDER_PRIORITY = {"eastmoney": 0, "yahoo": 1}


class SearchValidationError(ValueError):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(field)


class MarketSearchUnavailable(RuntimeError):
    pass


def _normalize_query(query: object) -> str:
    if not isinstance(query, str):
        raise SearchValidationError("q")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in query):
        raise SearchValidationError("q")
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", query)).strip()
    if not 1 <= len(normalized) <= 80:
        raise SearchValidationError("q")
    if any(
        not (character in _ALLOWED_PUNCTUATION or unicodedata.category(character)[0] in {"L", "N"})
        for character in normalized
    ):
        raise SearchValidationError("q")
    return normalized


def _validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise SearchValidationError("limit")
    return limit


def _safe_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and name == name.strip()
        and 1 <= len(name) <= 160
        and all(unicodedata.category(character)[0] != "C" for character in name)
    )


def _mapping_matches_candidate(candidate: InstrumentCandidate, mapping: ProviderSymbol) -> bool:
    try:
        if mapping.provider == "yahoo":
            if candidate.market is Market.HK:
                if _HK_PROVIDER_SYMBOL.fullmatch(mapping.symbol) is None:
                    return False
                code = mapping.symbol.removesuffix(".HK")
            elif candidate.market is Market.US:
                if _US_PROVIDER_SYMBOL.fullmatch(mapping.symbol) is None:
                    return False
                code = mapping.symbol
            else:
                return False
        elif mapping.provider == "eastmoney":
            market_id, separator, code = mapping.symbol.partition(".")
            expected_ids = {
                Market.CN_SH: frozenset({"1"}),
                Market.CN_SZ: frozenset({"0"}),
                Market.CN_BJ: frozenset({"0"}),
                Market.HK: frozenset({"116"}),
                Market.US: frozenset({"105", "106", "107"}),
            }[candidate.market]
            if separator != "." or market_id not in expected_ids:
                return False
            if candidate.market is Market.CN_SH:
                pattern = r"6\d{5}" if candidate.asset_type is AssetType.EQUITY else r"5\d{5}"
                if re.fullmatch(pattern, code) is None:
                    return False
            elif candidate.market is Market.CN_SZ:
                pattern = r"[03]\d{5}" if candidate.asset_type is AssetType.EQUITY else r"1\d{5}"
                if re.fullmatch(pattern, code) is None:
                    return False
            elif candidate.market is Market.CN_BJ:
                if candidate.asset_type is not AssetType.EQUITY or re.fullmatch(r"(?:[48]\d{5}|92\d{4})", code) is None:
                    return False
            elif candidate.market is Market.US and _US_PROVIDER_SYMBOL.fullmatch(code) is None:
                return False
            elif candidate.market is Market.HK and re.fullmatch(r"\d{1,5}", code) is None:
                return False
        else:
            return False
        return canonical_symbol(code, candidate.market) == candidate.canonical_symbol
    except (DomainValidationError, TypeError, ValueError):
        return False


def _trusted(candidate: object, provider: MarketDataProvider) -> InstrumentCandidate | None:
    if not isinstance(candidate, InstrumentCandidate) or candidate.candidate_id is not None:
        return None
    if type(candidate.canonical_symbol) is not str or type(candidate.name) is not str:
        return None
    if (
        not isinstance(candidate.market, Market)
        or not isinstance(candidate.currency, Currency)
        or not isinstance(candidate.asset_type, AssetType)
    ):
        return None
    if candidate.currency is not _EXPECTED_CURRENCY[candidate.market] or not _safe_name(candidate.name):
        return None
    try:
        if canonical_symbol(candidate.canonical_symbol, candidate.market) != candidate.canonical_symbol:
            return None
    except (DomainValidationError, TypeError, ValueError):
        return None
    if type(candidate.provider_symbols) is not tuple or len(candidate.provider_symbols) != 1:
        return None
    mapping = candidate.provider_symbols[0]
    if (
        not isinstance(mapping, ProviderSymbol)
        or type(mapping.provider) is not str
        or type(mapping.symbol) is not str
        or _PROVIDER_NAME.fullmatch(mapping.provider) is None
        or mapping.provider != provider.name
        or _PROVIDER_SYMBOL.fullmatch(mapping.symbol) is None
        or not _mapping_matches_candidate(candidate, mapping)
    ):
        return None
    return candidate


async def _provider_search(
    provider: MarketDataProvider,
    query: str,
    limit: int,
) -> tuple[list[InstrumentCandidate] | None, Exception | None]:
    try:
        candidates = await provider.search(query, limit=limit)
        if type(candidates) is not list:
            raise TypeError("provider search result must be a list")
        if len(candidates) > _MAX_PROVIDER_ITEMS:
            raise ValueError("provider search result exceeds the item limit")
        return candidates, None
    except Exception as error:
        return None, error


class MarketDataService:
    def __init__(
        self,
        database: Database,
        providers: Sequence[MarketDataProvider],
        repository: PortfolioRepository | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("at least one market-data provider is required")
        provider_names: set[str] = set()
        for provider in providers:
            name = getattr(provider, "name", None)
            if (
                type(name) is not str
                or _PROVIDER_NAME.fullmatch(name) is None
                or name not in _PROVIDER_PRIORITY
                or name in provider_names
            ):
                raise ValueError("providers must have unique reviewed names")
            provider_names.add(name)
        self._database = database
        self._providers = tuple(sorted(providers, key=lambda provider: _PROVIDER_PRIORITY.get(provider.name, 2)))
        self._repository = repository or PortfolioRepository()
        self._now = now or (lambda: datetime.now(UTC))

    async def search(self, query: str, limit: int) -> list[InstrumentCandidate]:
        normalized_query = _normalize_query(query)
        validated_limit = _validate_limit(limit)
        tasks: list[asyncio.Task[tuple[list[InstrumentCandidate] | None, Exception | None]]] = []
        async with asyncio.TaskGroup() as task_group:
            for provider in self._providers:
                tasks.append(task_group.create_task(_provider_search(provider, normalized_query, validated_limit)))
        results = [task.result() for task in tasks]
        if all(candidates is None for candidates, _ in results):
            raise MarketSearchUnavailable()

        merged: dict[str, InstrumentCandidate] = {}
        mappings: dict[str, dict[str, ProviderSymbol]] = {}
        for provider, (candidates, _) in zip(self._providers, results, strict=True):
            for untrusted in candidates or []:
                trusted = _trusted(untrusted, provider)
                if trusted is None:
                    continue
                existing = merged.get(trusted.canonical_symbol)
                if existing is None:
                    if len(merged) >= validated_limit:
                        continue
                    merged[trusted.canonical_symbol] = trusted
                    mapping = trusted.provider_symbols[0]
                    mappings[trusted.canonical_symbol] = {mapping.provider: mapping}
                elif (
                    existing.market,
                    existing.currency,
                    existing.asset_type,
                ) == (trusted.market, trusted.currency, trusted.asset_type):
                    mapping = trusted.provider_symbols[0]
                    if mapping.provider not in mappings[trusted.canonical_symbol]:
                        mappings[trusted.canonical_symbol][mapping.provider] = mapping

        candidates_to_cache = [
            replace(candidate, provider_symbols=tuple(mappings[symbol].values()))
            for symbol, candidate in merged.items()
        ]
        if not candidates_to_cache:
            return []
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        async with self._database.session() as session, session.begin():
            rows = await self._repository.cache_candidates(
                session,
                cast(Sequence[CandidateInput], candidates_to_cache),
                now=now,
            )
        return [
            replace(candidate, candidate_id=UUID(row.id))
            for candidate, row in zip(candidates_to_cache, rows, strict=True)
        ]
