"""Trusted search and explicit, bounded quote-refresh orchestration."""

import asyncio
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, cast
from uuid import UUID, uuid4

from vibe_portfolio.config import Settings
from vibe_portfolio.market_data.eastmoney import EastmoneySearchProvider
from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import (
    InstrumentCandidate,
    ProviderErrorCode,
    ProviderFailure,
    ProviderInstrument,
    ProviderQuote,
    ProviderSymbol,
    RefreshResult,
    RefreshScope,
    validate_quote,
)
from vibe_portfolio.market_data.protocol import MarketDataProvider
from vibe_portfolio.market_data.tencent import TencentQuoteProvider
from vibe_portfolio.market_data.yahoo import YahooSearchProvider
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import AssetType, Currency, DomainValidationError, Market, canonical_symbol
from vibe_portfolio.portfolio.repository import (
    CandidateInput,
    PortfolioRepository,
    RefreshInstrumentRecord,
    RefreshItemInput,
)
from vibe_portfolio.portfolio.tables import QuoteRefreshItemRow, QuoteRefreshRunRow

_WHITESPACE = re.compile(r"\s+")
_ALLOWED_PUNCTUATION = frozenset(" .-&/")
_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_PROVIDER_SYMBOL = re.compile(r"[A-Za-z0-9.^=-]{1,64}")
_US_PROVIDER_SYMBOL = re.compile(r"(?=.{1,15}\Z)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*(?:\.[A-Z0-9])?")
_HK_PROVIDER_SYMBOL = re.compile(r"\d{1,5}\.HK")
_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{8,128}$")
_MAX_PROVIDER_ITEMS = 25
_EXPECTED_CURRENCY = {
    Market.CN_SH: Currency.CNY,
    Market.CN_SZ: Currency.CNY,
    Market.CN_BJ: Currency.CNY,
    Market.HK: Currency.HKD,
    Market.US: Currency.USD,
}
_PROVIDER_PRIORITY = {"eastmoney": 0, "yahoo": 1, "tencent": 2}
_SEARCH_PROVIDERS = frozenset({"eastmoney", "yahoo"})
_ROUTES = {
    Market.CN_SH: ("eastmoney", "tencent"),
    Market.CN_SZ: ("eastmoney", "tencent"),
    Market.CN_BJ: ("eastmoney",),
    Market.HK: ("yahoo", "eastmoney"),
    Market.US: ("yahoo",),
}


class SearchValidationError(ValueError):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(field)


class RefreshValidationError(ValueError):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(field)


class MarketSearchUnavailable(RuntimeError):
    pass


class RefreshInProgress(RuntimeError):
    def __init__(self, run_id: UUID | None) -> None:
        self.run_id = run_id
        super().__init__("QUOTE_REFRESH_IN_PROGRESS")


class RefreshRunNotFound(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RefreshRunDetails:
    run: QuoteRefreshRunRow
    items: tuple[QuoteRefreshItemRow, ...]


@dataclass(frozen=True, slots=True)
class _Outcome:
    instrument_id: str
    outcome: str
    provider: str | None
    error_code: str | None
    quote: ProviderQuote | None


class ProviderRegistry:
    """Immutable registry containing only the three code-reviewed providers."""

    def __init__(
        self,
        providers: Sequence[MarketDataProvider],
        *,
        transports: Sequence[BoundedProviderHttp] = (),
    ) -> None:
        names = [getattr(provider, "name", None) for provider in providers]
        if len(providers) != 3 or set(names) != set(_PROVIDER_PRIORITY) or len(set(names)) != 3:
            raise ValueError("registry must contain each reviewed provider exactly once")
        ordered = sorted(providers, key=lambda provider: _PROVIDER_PRIORITY[provider.name])
        self._providers = tuple(ordered)
        self._by_name = MappingProxyType({provider.name: provider for provider in ordered})
        self._transports = tuple(transports)

    @property
    def providers(self) -> tuple[MarketDataProvider, ...]:
        return self._providers

    def get(self, name: str) -> MarketDataProvider:
        return self._by_name[name]

    async def aclose(self) -> None:
        await asyncio.gather(*(transport.aclose() for transport in self._transports))


def build_live_provider_registry(settings: Settings) -> ProviderRegistry:
    eastmoney_http = BoundedProviderHttp(
        allowed_hosts={"searchapi.eastmoney.com", "push2.eastmoney.com"}, settings=settings
    )
    yahoo_http = BoundedProviderHttp(
        allowed_hosts={"query1.finance.yahoo.com", "query2.finance.yahoo.com"}, settings=settings
    )
    tencent_http = BoundedProviderHttp(allowed_hosts={"qt.gtimg.cn"}, settings=settings)
    return ProviderRegistry(
        (
            EastmoneySearchProvider(eastmoney_http),
            YahooSearchProvider(yahoo_http),
            TencentQuoteProvider(tencent_http),
        ),
        transports=(eastmoney_http, yahoo_http, tencent_http),
    )


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
    provider: MarketDataProvider, query: str, limit: int
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
        providers: Sequence[MarketDataProvider] | ProviderRegistry,
        repository: PortfolioRepository | None = None,
        *,
        settings: Settings | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        supplied = providers.providers if isinstance(providers, ProviderRegistry) else tuple(providers)
        if not supplied:
            raise ValueError("at least one market-data provider is required")
        names: set[str] = set()
        for provider in supplied:
            name = getattr(provider, "name", None)
            if (
                type(name) is not str
                or _PROVIDER_NAME.fullmatch(name) is None
                or name not in _PROVIDER_PRIORITY
                or name in names
            ):
                raise ValueError("providers must have unique reviewed names")
            names.add(name)
        self._database = database
        self._registry = providers if isinstance(providers, ProviderRegistry) else None
        self._providers = tuple(
            sorted(
                (provider for provider in supplied if provider.name in _SEARCH_PROVIDERS),
                key=lambda p: _PROVIDER_PRIORITY[p.name],
            )
        )
        self._repository = repository or PortfolioRepository()
        self._settings = settings or Settings()
        self._now = now or (lambda: datetime.now(UTC))
        self._refresh_lock = asyncio.Lock()

    async def startup(self) -> None:
        now = self._aware_now()
        async with self._database.session() as session, session.begin():
            await self._repository.abandon_running_refreshes(session, now)
            await self._repository.prune_expired(session, now)

    async def search(self, query: str, limit: int) -> list[InstrumentCandidate]:
        normalized_query, validated_limit = _normalize_query(query), _validate_limit(limit)
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
                elif (existing.market, existing.currency, existing.asset_type) == (
                    trusted.market,
                    trusted.currency,
                    trusted.asset_type,
                ):
                    mapping = trusted.provider_symbols[0]
                    mappings[trusted.canonical_symbol].setdefault(mapping.provider, mapping)
        candidates_to_cache = [
            replace(candidate, provider_symbols=tuple(mappings[symbol].values()))
            for symbol, candidate in merged.items()
        ]
        if not candidates_to_cache:
            return []
        now = self._aware_now()
        async with self._database.session() as session, session.begin():
            rows = await self._repository.cache_candidates(
                session, cast(Sequence[CandidateInput], candidates_to_cache), now=now
            )
        return [
            replace(candidate, candidate_id=UUID(row.id))
            for candidate, row in zip(candidates_to_cache, rows, strict=True)
        ]

    async def refresh(self, scope: RefreshScope, idempotency_key: str) -> RefreshResult:
        if self._registry is None:
            raise RuntimeError("quote refresh requires the fixed provider registry")
        ids = self._validate_refresh_input(scope, idempotency_key)
        request_hash = sha256(
            json.dumps(None if ids is None else sorted(ids), separators=(",", ":")).encode()
        ).hexdigest()
        replay = await self._replay(idempotency_key, request_hash)
        if replay is not None:
            return replay
        if self._refresh_lock.locked():
            raise RefreshInProgress(await self._running_id())
        async with self._refresh_lock:
            replay = await self._replay(idempotency_key, request_hash)
            if replay is not None:
                return replay
            started_at = self._aware_now()
            async with self._database.session() as session, session.begin():
                running = await self._repository.running_refresh(session)
                if running is not None:
                    raise RefreshInProgress(UUID(running.id))
                records = await self._repository.active_refresh_instruments(session, ids)
                if ids is not None and len(records) != len(ids):
                    raise RefreshValidationError("instrument_ids")
                if len(records) > self._settings.market_max_batch_instruments:
                    raise RefreshValidationError("instrument_ids")
                claim = await self._repository.claim_idempotency(
                    session, "market-data:refresh", idempotency_key, request_hash, started_at
                )
                if claim.completed:
                    run = await self._repository.refresh_run(session, claim.row.resource_id or "")
                    if run is None:
                        raise RuntimeError("refresh replay unavailable")
                    return _refresh_result(run)
                run_id = str(uuid4())
                scope_hash = sha256(
                    json.dumps(sorted(record.instrument.id for record in records), separators=(",", ":")).encode()
                ).hexdigest()
                session.add(
                    QuoteRefreshRunRow(
                        id=run_id,
                        scope_hash=scope_hash,
                        status="running",
                        started_at=started_at,
                        finished_at=None,
                        updated_count=0,
                        stale_count=0,
                        unavailable_count=0,
                    )
                )
            outcomes = await self._fetch_all(records)
            status = _terminal_status(outcomes)
            finished_at = self._aware_now()
            items = [_item(outcome) for outcome in outcomes]
            async with self._database.session() as session, session.begin():
                await self._repository.complete_refresh(
                    session, run_id=run_id, status=status, items=items, finished_at=finished_at, claim=claim
                )
                await self._repository.prune_expired(session, finished_at)
            public_status: Literal["succeeded", "partial", "failed"] = "succeeded" if status == "completed" else status
            return RefreshResult(
                run_id=UUID(run_id),
                status=public_status,
                updated=sum(item.outcome == "updated" for item in outcomes),
                stale=sum(item.outcome == "stale" for item in outcomes),
                unavailable=sum(item.outcome == "unavailable" for item in outcomes),
            )

    async def refresh_run(self, run_id: UUID) -> RefreshRunDetails:
        async with self._database.session() as session:
            run = await self._repository.refresh_run(session, str(run_id))
            if run is None:
                raise RefreshRunNotFound()
            items = await self._repository.refresh_items(session, str(run_id))
            return RefreshRunDetails(run=run, items=tuple(items))

    def _validate_refresh_input(self, scope: RefreshScope, key: str) -> list[str] | None:
        if not isinstance(scope, RefreshScope) or _IDEMPOTENCY_KEY.fullmatch(key) is None:
            raise RefreshValidationError("Idempotency-Key")
        if scope.instrument_ids is None:
            return None
        if len(scope.instrument_ids) > self._settings.market_max_batch_instruments:
            raise RefreshValidationError("instrument_ids")
        ids = [str(value) for value in scope.instrument_ids]
        if len(ids) != len(set(ids)):
            raise RefreshValidationError("instrument_ids")
        return ids

    async def _replay(self, key: str, request_hash: str) -> RefreshResult | None:
        now = self._aware_now()
        async with self._database.session() as session:
            row = await self._repository.completed_idempotency(session, "market-data:refresh", key, request_hash, now)
            if row is None:
                return None
            run = await self._repository.refresh_run(session, row.resource_id or "")
            if run is None:
                raise RuntimeError("refresh replay unavailable")
            return _refresh_result(run)

    async def _running_id(self) -> UUID | None:
        async with self._database.session() as session:
            run = await self._repository.running_refresh(session)
            return None if run is None else UUID(run.id)

    async def _fetch_all(self, records: Sequence[RefreshInstrumentRecord]) -> list[_Outcome]:
        if not records:
            return []
        semaphore = asyncio.Semaphore(self._settings.market_max_concurrency)
        results: dict[str, _Outcome] = {}
        route_groups: dict[tuple[str, ...], list[RefreshInstrumentRecord]] = {}
        for record in records:
            route_groups.setdefault(_ROUTES[Market(record.instrument.market)], []).append(record)
        tasks = [
            asyncio.create_task(self._fetch_route(group, route, semaphore, results))
            for route, group in route_groups.items()
        ]
        try:
            async with asyncio.timeout(self._settings.market_operation_timeout_seconds):
                await asyncio.gather(*tasks)
        except TimeoutError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return [
            results.get(record.instrument.id, _failed_outcome(record, None, ProviderErrorCode.TIMEOUT.value))
            for record in records
        ]

    async def _fetch_route(
        self,
        records: Sequence[RefreshInstrumentRecord],
        route: tuple[str, ...],
        semaphore: asyncio.Semaphore,
        results: dict[str, _Outcome],
    ) -> None:
        pending = {record.instrument.id: record for record in records}
        last_provider: dict[str, str] = {}
        last_error = {record.instrument.id: "QUOTE_UNAVAILABLE" for record in records}
        for provider_name in route:
            requested: list[tuple[RefreshInstrumentRecord, ProviderInstrument]] = []
            for record in pending.values():
                provider_instrument = _refresh_provider_instrument(record, provider_name)
                if provider_instrument is not None:
                    requested.append((record, provider_instrument))
                    last_provider[record.instrument.id] = provider_name
            if not requested:
                continue
            provider = self._registry.get(provider_name) if self._registry is not None else None
            assert provider is not None
            try:
                async with semaphore:
                    values = await provider.fetch_quotes([instrument for _, instrument in requested])
                if type(values) is not list or len(values) > len(requested):
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID)
            except ProviderFailure as error:
                for record, _ in requested:
                    last_error[record.instrument.id] = error.code.value
                continue
            except Exception:
                for record, _ in requested:
                    last_error[record.instrument.id] = ProviderErrorCode.RESPONSE_INVALID.value
                continue

            lookup = {
                (instrument.canonical_symbol, instrument.provider_symbol): (record, instrument)
                for record, instrument in requested
            }
            accepted: dict[str, ProviderQuote] = {}
            invalid: set[str] = set()
            for quote in values:
                try:
                    matched = lookup.get((quote.canonical_symbol, quote.provider_symbol))
                    if matched is None:
                        continue
                    record, instrument = matched
                    if record.instrument.id in accepted or quote.provider != provider_name:
                        invalid.add(record.instrument.id)
                        continue
                    accepted[record.instrument.id] = validate_quote(quote, instrument, self._aware_now())
                except ProviderFailure:
                    if matched is not None:
                        invalid.add(matched[0].instrument.id)
                except Exception:
                    continue
            for instrument_id in invalid:
                accepted.pop(instrument_id, None)
                last_error[instrument_id] = ProviderErrorCode.RESPONSE_INVALID.value
            for instrument_id, quote in accepted.items():
                results[instrument_id] = _Outcome(instrument_id, "updated", provider_name, None, quote)
                pending.pop(instrument_id, None)
        for instrument_id, record in pending.items():
            results[instrument_id] = _failed_outcome(
                record,
                last_provider.get(instrument_id),
                last_error[instrument_id],
            )

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value.astimezone(UTC)


def _refresh_provider_instrument(record: RefreshInstrumentRecord, provider: str) -> ProviderInstrument | None:
    row = record.instrument
    market, currency, asset_type = Market(row.market), Currency(row.currency), AssetType(row.asset_type)
    provider_symbol: str | None
    if provider == "tencent":
        if market not in {Market.CN_SH, Market.CN_SZ}:
            return None
        code = row.canonical_symbol.rsplit(".", 1)[0]
        provider_symbol = f"{'sh' if market is Market.CN_SH else 'sz'}{code}"
    else:
        provider_symbol = record.mappings.get(provider)
        if provider_symbol is None:
            return None
        candidate = InstrumentCandidate(
            row.canonical_symbol, row.name, market, currency, asset_type, (ProviderSymbol(provider, provider_symbol),)
        )
        if not _mapping_matches_candidate(candidate, candidate.provider_symbols[0]):
            return None
    return ProviderInstrument(row.canonical_symbol, provider_symbol, market, currency, asset_type)


def _failed_outcome(record: RefreshInstrumentRecord, provider: str | None, error: str) -> _Outcome:
    return _Outcome(record.instrument.id, "stale" if record.has_quote else "unavailable", provider, error, None)


def _terminal_status(outcomes: Sequence[_Outcome]) -> Literal["completed", "partial", "failed"]:
    updated = sum(item.outcome == "updated" for item in outcomes)
    if not outcomes or updated == len(outcomes):
        return "completed"
    return "partial" if updated else "failed"


def _item(outcome: _Outcome) -> RefreshItemInput:
    quote = outcome.quote
    return RefreshItemInput(
        instrument_id=outcome.instrument_id,
        outcome=outcome.outcome,
        provider=outcome.provider,
        error_code=outcome.error_code,
        price=None if quote is None else quote.price,
        currency=None if quote is None else quote.currency.value,
        provider_symbol=None if quote is None else quote.provider_symbol,
        as_of=None if quote is None else quote.as_of.astimezone(UTC),
    )


def _refresh_result(run: QuoteRefreshRunRow) -> RefreshResult:
    if run.status == "completed":
        public: Literal["succeeded", "partial", "failed"] = "succeeded"
    elif run.status == "partial":
        public = "partial"
    elif run.status == "failed":
        public = "failed"
    else:
        raise RuntimeError("refresh run is not terminal")
    return RefreshResult(
        run_id=UUID(run.id),
        status=public,
        updated=run.updated_count,
        stale=run.stale_count,
        unavailable=run.unavailable_count,
    )
