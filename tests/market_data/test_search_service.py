import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import vibe_portfolio.market_data.service as service_module
from vibe_portfolio.market_data.models import (
    InstrumentCandidate,
    ProviderInstrument,
    ProviderQuote,
    ProviderSymbol,
)
from vibe_portfolio.market_data.service import MarketDataService, MarketSearchUnavailable, SearchValidationError
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.repository import PortfolioRepository
from vibe_portfolio.portfolio.tables import InstrumentCandidateRow


class FakeProvider:
    def __init__(self, name: str, candidates: object = None, *, fails: bool = False) -> None:
        self.name = name
        self.candidates = [] if candidates is None else candidates
        self.fails = fails
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        self.calls.append((query, limit))
        if self.fails:
            raise RuntimeError("synthetic provider failure")
        return cast(list[InstrumentCandidate], self.candidates)

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        return []


def candidate(
    symbol: str,
    provider: str,
    *,
    name: str = "Fictional Demo Systems",
    market: Market = Market.US,
    currency: Currency = Currency.USD,
    asset_type: AssetType = AssetType.EQUITY,
) -> InstrumentCandidate:
    base = symbol.rsplit(".", 1)[0]
    if provider == "eastmoney":
        market_id = {
            Market.CN_SH: "1",
            Market.CN_SZ: "0",
            Market.CN_BJ: "0",
            Market.HK: "116",
            Market.US: "105",
        }[market]
        provider_symbol = f"{market_id}.{base}"
    elif market is Market.HK:
        provider_symbol = f"{base.lstrip('0') or '0'}.HK"
    else:
        provider_symbol = base
    return InstrumentCandidate(
        canonical_symbol=symbol,
        name=name,
        market=market,
        currency=currency,
        asset_type=asset_type,
        provider_symbols=(ProviderSymbol(provider, provider_symbol),),
    )


@pytest_asyncio.fixture
async def database(tmp_path: pytest.TempPathFactory) -> Database:
    database = Database(tmp_path / "search.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


async def test_search_normalizes_query_merges_duplicates_and_caches_exactly_fifteen_minutes(
    database: Database,
) -> None:
    now = datetime(2026, 7, 19, 3, 4, tzinfo=UTC)
    eastmoney = FakeProvider("eastmoney", [candidate("DEMO.US", "eastmoney"), candidate("ALPHA.US", "eastmoney")])
    yahoo = FakeProvider(
        "yahoo",
        [candidate("DEMO.US", "yahoo", name="Different untrusted name"), candidate("BETA.US", "yahoo")],
    )
    service = MarketDataService(database, (eastmoney, yahoo), now=lambda: now)

    results = await service.search("  Ｄｅｍｏ\u3000/  Labs  ", 3)

    assert eastmoney.calls == [("Demo / Labs", 3)]
    assert yahoo.calls == [("Demo / Labs", 3)]
    assert [result.canonical_symbol for result in results] == ["DEMO.US", "ALPHA.US", "BETA.US"]
    assert results[0].name == "Fictional Demo Systems"
    assert results[0].sources == ("eastmoney", "yahoo")
    assert all(result.candidate_id is not None for result in results)
    async with database.session() as session:
        rows = (await session.scalars(select(InstrumentCandidateRow).order_by(InstrumentCandidateRow.created_at))).all()
    assert len(rows) == 3
    assert all(row.created_at == now and row.expires_at == now + timedelta(minutes=15) for row in rows)


async def test_search_keeps_partial_provider_success_and_empty_success(database: Database) -> None:
    eastmoney = FakeProvider("eastmoney", fails=True)
    yahoo = FakeProvider("yahoo", [candidate("DEMO.US", "yahoo")])
    service = MarketDataService(database, (eastmoney, yahoo))
    assert [item.canonical_symbol for item in await service.search("demo", 5)] == ["DEMO.US"]

    empty = MarketDataService(database, (FakeProvider("eastmoney"), FakeProvider("yahoo")))
    assert await empty.search("missing", 5) == []


async def test_successful_candidate_cache_prunes_expired_rows(database: Database) -> None:
    now = datetime(2026, 7, 19, 3, 4, tzinfo=UTC)
    expired_id = str(uuid4())
    async with database.session() as session, session.begin():
        session.add(
            InstrumentCandidateRow(
                id=expired_id,
                canonical_symbol="OLD.US",
                name="Old",
                market="US",
                currency="USD",
                asset_type="equity",
                provider="yahoo",
                provider_symbols_json='[{"provider":"yahoo","symbol":"OLD"}]',
                created_at=now - timedelta(minutes=20),
                expires_at=now - timedelta(minutes=5),
                consumed_at=None,
            )
        )
    service = MarketDataService(
        database,
        (FakeProvider("eastmoney"), FakeProvider("yahoo", [candidate("DEMO.US", "yahoo")])),
        now=lambda: now,
    )
    await service.search("demo", 5)
    async with database.session() as session:
        assert await session.get(InstrumentCandidateRow, expired_id) is None


async def test_search_raises_only_when_all_providers_fail(database: Database) -> None:
    service = MarketDataService(
        database,
        (FakeProvider("eastmoney", fails=True), FakeProvider("yahoo", fails=True)),
    )
    with pytest.raises(MarketSearchUnavailable):
        await service.search("demo", 5)


@pytest.mark.parametrize(
    ("query", "limit"),
    [
        ("", 5),
        ("x" * 81, 5),
        ("demo?", 5),
        ("https://example.invalid", 5),
        ("demo\nname", 5),
        ("demo\tname", 5),
        ("demo\x00", 5),
        ("demo", 0),
        ("demo", 26),
        ("demo", True),
    ],
)
async def test_search_rejects_unbounded_or_unsafe_input(database: Database, query: str, limit: int) -> None:
    service = MarketDataService(database, (FakeProvider("eastmoney"), FakeProvider("yahoo")))
    with pytest.raises(SearchValidationError):
        await service.search(query, limit)


async def test_search_allows_normalized_unicode_letters_numbers_and_reviewed_punctuation(database: Database) -> None:
    eastmoney = FakeProvider("eastmoney")
    yahoo = FakeProvider("yahoo")
    service = MarketDataService(database, (eastmoney, yahoo))
    assert await service.search("  示例\u3000１２３.A-B & C/D  ", 5) == []
    assert eastmoney.calls == [("示例 123.A-B & C/D", 5)]
    assert yahoo.calls == [("示例 123.A-B & C/D", 5)]


async def test_search_drops_untrusted_provider_candidates(database: Database) -> None:
    valid = candidate("DEMO.US", "eastmoney")
    invalid_identity = replace(valid, canonical_symbol="NOT A SYMBOL.US")
    invalid_currency = replace(candidate("ALPHA.US", "eastmoney"), currency=Currency.CNY)
    invalid_asset_type = replace(candidate("GAMMA.US", "eastmoney"), asset_type=cast(AssetType, "crypto"))
    wrong_provenance = candidate("BETA.US", "unexpected")
    service = MarketDataService(
        database,
        (
            FakeProvider(
                "eastmoney",
                [invalid_identity, invalid_currency, invalid_asset_type, wrong_provenance, valid],
            ),
            FakeProvider("yahoo"),
        ),
    )
    assert [item.canonical_symbol for item in await service.search("demo", 5)] == ["DEMO.US"]


async def test_search_drops_malicious_runtime_fields_without_losing_valid_partial_results(
    database: Database,
) -> None:
    valid = candidate("DEMO.US", "eastmoney")
    malicious = [
        replace(valid, canonical_symbol=cast(Any, 7)),
        replace(valid, name=cast(Any, 7)),
        replace(valid, name="bad\nname"),
        replace(valid, provider_symbols=cast(Any, (object(),))),
        replace(valid, provider_symbols=(ProviderSymbol(cast(Any, 7), "105.DEMO"),)),
        replace(valid, provider_symbols=(ProviderSymbol("eastmoney\n", "105.DEMO"),)),
        replace(valid, provider_symbols=(ProviderSymbol("eastmoney", cast(Any, 7)),)),
        replace(valid, provider_symbols=(ProviderSymbol("eastmoney", "105.OTHER"),)),
    ]
    service = MarketDataService(
        database,
        (FakeProvider("eastmoney", [*malicious, valid]), FakeProvider("yahoo", fails=True)),
    )
    results = await service.search("demo", 5)
    assert [item.canonical_symbol for item in results] == ["DEMO.US"]


@pytest.mark.parametrize("provider_name", [cast(Any, 7), "", "YAHOO", "bad name", "x" * 33, "custom"])
async def test_search_rejects_unsafe_or_unreviewed_provider_names(
    database: Database,
    provider_name: object,
) -> None:
    with pytest.raises(ValueError):
        MarketDataService(database, (FakeProvider(cast(str, provider_name)),))


@pytest.mark.parametrize(
    "untrusted",
    [
        candidate("DEMO.US", "eastmoney"),
        candidate("00999.HK", "yahoo", market=Market.HK, currency=Currency.HKD),
    ],
)
async def test_search_caches_only_provider_symbols_consistent_with_canonical_identity(
    database: Database,
    untrusted: InstrumentCandidate,
) -> None:
    mapping = untrusted.provider_symbols[0]
    bad_symbol = "105.OTHER" if mapping.provider == "eastmoney" else "0999.HK.EXTRA"
    invalid = replace(untrusted, provider_symbols=(ProviderSymbol(mapping.provider, bad_symbol),))
    service = MarketDataService(
        database,
        (FakeProvider(mapping.provider, [invalid, untrusted]),),
    )
    results = await service.search("demo", 5)
    assert len(results) == 1
    assert results[0].canonical_symbol == untrusted.canonical_symbol
    assert results[0].provider_symbols == untrusted.provider_symbols


async def test_search_rejects_oversized_or_non_list_provider_results_without_scanning(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_trusted = service_module._trusted
    scanned = 0

    def trap(untrusted: object, provider: object) -> InstrumentCandidate | None:
        nonlocal scanned
        if getattr(provider, "name", None) == "eastmoney":
            scanned += 1
            pytest.fail("oversized provider result was scanned")
        return original_trusted(untrusted, cast(Any, provider))

    monkeypatch.setattr(service_module, "_trusted", trap)
    traps = [object() for _ in range(26)]
    valid = FakeProvider("yahoo", [candidate("DEMO.US", "yahoo")])
    oversized = MarketDataService(database, (FakeProvider("eastmoney", traps), valid))
    assert [item.canonical_symbol for item in await oversized.search("demo", 5)] == ["DEMO.US"]
    assert scanned == 0

    def generator() -> object:
        pytest.fail("provider generator was scanned")
        yield candidate("ALPHA.US", "eastmoney")

    all_invalid = MarketDataService(
        database,
        (FakeProvider("eastmoney", generator()), FakeProvider("yahoo", generator())),
    )
    with pytest.raises(MarketSearchUnavailable):
        await all_invalid.search("demo", 5)


class CoordinatedProvider(FakeProvider):
    def __init__(
        self,
        name: str,
        entered: asyncio.Event,
        peer_entered: asyncio.Event,
        release: asyncio.Event,
        cancelled: asyncio.Event,
    ) -> None:
        super().__init__(name, [candidate("DEMO.US", name)])
        self.entered = entered
        self.peer_entered = peer_entered
        self.release = release
        self.cancelled = cancelled

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        self.entered.set()
        try:
            await asyncio.wait_for(self.peer_entered.wait(), timeout=1)
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return cast(list[InstrumentCandidate], self.candidates)


def coordinated_service(
    database: Database,
) -> tuple[
    MarketDataService,
    asyncio.Event,
    tuple[asyncio.Event, asyncio.Event],
    tuple[asyncio.Event, asyncio.Event],
]:
    east_entered, yahoo_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    east_cancelled, yahoo_cancelled = asyncio.Event(), asyncio.Event()
    service = MarketDataService(
        database,
        (
            CoordinatedProvider("eastmoney", east_entered, yahoo_entered, release, east_cancelled),
            CoordinatedProvider("yahoo", yahoo_entered, east_entered, release, yahoo_cancelled),
        ),
    )
    return service, release, (east_entered, yahoo_entered), (east_cancelled, yahoo_cancelled)


async def test_search_provider_calls_overlap(database: Database) -> None:
    service, release, entered, _ = coordinated_service(database)
    search = asyncio.create_task(service.search("demo", 5))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=1)
    release.set()
    results = await asyncio.wait_for(search, timeout=1)
    assert results[0].sources == ("eastmoney", "yahoo")


async def test_search_cancellation_cancels_both_providers_and_caches_nothing(database: Database) -> None:
    service, _, entered, cancelled = coordinated_service(database)
    search = asyncio.create_task(service.search("demo", 5))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=1)
    search.cancel()
    with pytest.raises(asyncio.CancelledError):
        await search
    assert all(event.is_set() for event in cancelled)
    async with database.session() as session:
        assert list(await session.scalars(select(InstrumentCandidateRow))) == []


class FailingCacheRepository(PortfolioRepository):
    async def cache_candidates(
        self,
        session: AsyncSession,
        candidates: Sequence[Any],
        *,
        now: datetime,
    ) -> list[InstrumentCandidateRow]:
        await super().cache_candidates(session, candidates, now=now)
        raise RuntimeError("synthetic cache failure")


async def test_search_cache_failure_rolls_back_all_candidates(database: Database) -> None:
    service = MarketDataService(
        database,
        (FakeProvider("eastmoney", [candidate("DEMO.US", "eastmoney")]),),
        FailingCacheRepository(),
    )
    with pytest.raises(RuntimeError, match="synthetic cache failure"):
        await service.search("demo", 5)
    async with database.session() as session:
        assert list(await session.scalars(select(InstrumentCandidateRow))) == []


async def test_search_rank_is_eastmoney_then_yahoo_regardless_of_injection_order(database: Database) -> None:
    service = MarketDataService(
        database,
        (
            FakeProvider("yahoo", [candidate("DEMO.US", "yahoo", name="Yahoo name")]),
            FakeProvider("eastmoney", [candidate("DEMO.US", "eastmoney", name="Eastmoney name")]),
        ),
    )
    results = await service.search("demo", 5)
    assert results[0].name == "Eastmoney name"
    assert results[0].sources == ("eastmoney", "yahoo")


async def test_search_same_provider_duplicate_conflict_keeps_first_result(database: Database) -> None:
    first = candidate("DEMO.US", "eastmoney", name="First name")
    second = candidate("DEMO.US", "eastmoney", name="Second name")
    service = MarketDataService(database, (FakeProvider("eastmoney", [first, second]),))
    results = await service.search("demo", 5)
    assert len(results) == 1
    assert results[0].name == "First name"
    assert results[0].provider_symbols == first.provider_symbols
