import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from vibe_portfolio.config import Settings
from vibe_portfolio.market_data.models import InstrumentCandidate, ProviderInstrument, ProviderQuote, RefreshScope
from vibe_portfolio.market_data.service import (
    MarketDataService,
    ProviderRegistry,
    RefreshInProgress,
    RefreshOperationTimeout,
)
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.repository import PortfolioRepository, hash_idempotency_key
from vibe_portfolio.portfolio.tables import (
    AccountRow,
    IdempotencyRow,
    InstrumentCandidateRow,
    InstrumentProviderSymbolRow,
    InstrumentRow,
    LatestQuoteRow,
    PositionRow,
    QuoteRefreshItemRow,
    QuoteRefreshRunRow,
)

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


class FakeProvider:
    def __init__(self, name: str, *, missing: set[str] | None = None, invalid: set[str] | None = None) -> None:
        self.name = name
        self.missing = missing or set()
        self.invalid = invalid or set()
        self.calls: list[tuple[str, ...]] = []
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        return []

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        self.calls.append(tuple(item.canonical_symbol for item in instruments))
        if self.entered is not None and self.release is not None:
            self.entered.set()
            await self.release.wait()
        return [
            ProviderQuote(
                canonical_symbol=(
                    "OTHER.US" if instrument.canonical_symbol in self.invalid else instrument.canonical_symbol
                ),
                provider_symbol=instrument.provider_symbol,
                price=Decimal("51.250001"),
                currency=instrument.currency,
                as_of=NOW - timedelta(minutes=1),
                provider=self.name,
            )
            for instrument in instruments
            if instrument.canonical_symbol not in self.missing
        ]


@pytest_asyncio.fixture
async def database(tmp_path: pytest.TempPathFactory) -> AsyncIterator[Database]:
    database = Database(tmp_path / "refresh.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


def registry(
    *, east: FakeProvider | None = None, yahoo: FakeProvider | None = None, tencent: FakeProvider | None = None
) -> ProviderRegistry:
    return ProviderRegistry(
        (east or FakeProvider("eastmoney"), yahoo or FakeProvider("yahoo"), tencent or FakeProvider("tencent"))
    )


async def seed_active(
    database: Database,
    specifications: Sequence[tuple[str, Market, Currency, dict[str, str]]],
) -> list[UUID]:
    ids: list[UUID] = []
    async with database.session() as session, session.begin():
        for index, (symbol, market, currency, mappings) in enumerate(specifications):
            instrument_id, account_id, position_id = uuid4(), uuid4(), uuid4()
            ids.append(instrument_id)
            session.add(
                AccountRow(
                    id=str(account_id),
                    name=f"Account {index}",
                    normalized_name=f"account {index}",
                    currency=currency.value,
                    cash_balance=Decimal("0"),
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                    archived_at=None,
                )
            )
            session.add(
                InstrumentRow(
                    id=str(instrument_id),
                    canonical_symbol=symbol,
                    name=f"Instrument {index}",
                    market=market.value,
                    currency=currency.value,
                    asset_type=AssetType.EQUITY.value,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await session.flush()
            for provider, provider_symbol in mappings.items():
                session.add(
                    InstrumentProviderSymbolRow(
                        instrument_id=str(instrument_id), provider=provider, provider_symbol=provider_symbol
                    )
                )
            session.add(
                PositionRow(
                    id=str(position_id),
                    account_id=str(account_id),
                    instrument_id=str(instrument_id),
                    quantity=Decimal("1"),
                    average_cost=Decimal("1"),
                    note=None,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                    archived_at=None,
                )
            )
    return ids


async def seed_expired_retention(database: Database, instrument_id: UUID) -> tuple[str, tuple[str, str], str]:
    candidate_id = str(uuid4())
    old_run_id = str(uuid4())
    key_hash = hash_idempotency_key("expired-retention-key")
    async with database.session() as session, session.begin():
        session.add(
            InstrumentCandidateRow(
                id=candidate_id,
                canonical_symbol="OLD.US",
                name="Old",
                market="US",
                currency="USD",
                asset_type="equity",
                provider="yahoo",
                provider_symbols_json='[{"provider":"yahoo","symbol":"OLD"}]',
                created_at=NOW - timedelta(minutes=20),
                expires_at=NOW - timedelta(minutes=5),
                consumed_at=None,
            )
        )
        session.add(
            QuoteRefreshRunRow(
                id=old_run_id,
                scope_hash="9" * 64,
                status="failed",
                started_at=NOW - timedelta(days=91, minutes=1),
                finished_at=NOW - timedelta(days=91),
                updated_count=0,
                stale_count=0,
                unavailable_count=1,
                scope_json=None,
                terminal_error="QUOTE_UNAVAILABLE",
            )
        )
        session.add(
            IdempotencyRow(
                scope="expired:test",
                key_hash=key_hash,
                request_hash="8" * 64,
                state="completed",
                resource_id=None,
                resource_version=None,
                response_status=200,
                created_at=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(days=1),
            )
        )
        await session.flush()
        session.add(
            QuoteRefreshItemRow(
                run_id=old_run_id,
                instrument_id=str(instrument_id),
                outcome="unavailable",
                provider=None,
                error_code="QUOTE_UNAVAILABLE",
                created_at=NOW - timedelta(days=91),
            )
        )
    return candidate_id, ("expired:test", key_hash), old_run_id


async def test_fixed_routes_and_valid_primary_prevents_fallback(database: Database) -> None:
    await seed_active(
        database,
        [
            ("600000.SH", Market.CN_SH, Currency.CNY, {"eastmoney": "1.600000"}),
            ("000001.SZ", Market.CN_SZ, Currency.CNY, {"eastmoney": "0.000001"}),
            ("499991.BJ", Market.CN_BJ, Currency.CNY, {"eastmoney": "0.499991"}),
            ("00700.HK", Market.HK, Currency.HKD, {"yahoo": "0700.HK", "eastmoney": "116.00700"}),
            ("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"}),
        ],
    )
    east, yahoo, tencent = FakeProvider("eastmoney"), FakeProvider("yahoo"), FakeProvider("tencent")
    result = await MarketDataService(
        database, registry(east=east, yahoo=yahoo, tencent=tencent), now=lambda: NOW
    ).refresh(RefreshScope.all(), "refresh-routes")
    assert result.status == "succeeded"
    assert result.updated == 5
    assert {frozenset(call) for call in east.calls} == {
        frozenset({"600000.SH", "000001.SZ"}),
        frozenset({"499991.BJ"}),
    }
    assert set(yahoo.calls) == {("00700.HK",), ("DEMO.US",)}
    assert tencent.calls == []
    async with database.session() as session:
        run = await session.get(QuoteRefreshRunRow, str(result.run_id))
    assert run is not None and run.scope_json is None


async def test_invalid_primary_falls_back_only_for_that_instrument(database: Database) -> None:
    await seed_active(
        database,
        [
            ("600000.SH", Market.CN_SH, Currency.CNY, {"eastmoney": "1.600000"}),
            ("000001.SZ", Market.CN_SZ, Currency.CNY, {"eastmoney": "0.000001"}),
        ],
    )
    east = FakeProvider("eastmoney", invalid={"600000.SH"})
    tencent = FakeProvider("tencent")
    result = await MarketDataService(database, registry(east=east, tencent=tencent), now=lambda: NOW).refresh(
        RefreshScope.all(), "refresh-fallback"
    )
    assert result.updated == 2
    assert tencent.calls == [("600000.SH",)]


async def test_partial_refresh_preserves_last_valid_quote(database: Database) -> None:
    ids = await seed_active(
        database,
        [
            ("FRESH.US", Market.US, Currency.USD, {"yahoo": "FRESH"}),
            ("STALE.US", Market.US, Currency.USD, {"yahoo": "STALE"}),
        ],
    )
    prior_run = str(uuid4())
    async with database.session() as session, session.begin():
        session.add(
            QuoteRefreshRunRow(
                id=prior_run,
                scope_hash="0" * 64,
                status="completed",
                started_at=NOW - timedelta(days=1),
                finished_at=NOW - timedelta(days=1),
                updated_count=1,
                stale_count=0,
                unavailable_count=0,
            )
        )
        await session.flush()
        session.add(
            LatestQuoteRow(
                instrument_id=str(ids[1]),
                price=Decimal("42.10"),
                currency="USD",
                provider="yahoo",
                provider_symbol="STALE",
                as_of=NOW - timedelta(days=1),
                fetched_at=NOW - timedelta(days=1),
                refresh_run_id=prior_run,
            )
        )
    yahoo = FakeProvider("yahoo", missing={"STALE.US"})
    result = await MarketDataService(database, registry(yahoo=yahoo), now=lambda: NOW).refresh(
        RefreshScope.all(), "refresh-partial"
    )
    assert (result.status, result.updated, result.stale, result.unavailable) == ("partial", 1, 1, 0)
    async with database.session() as session:
        quote = await session.get(LatestQuoteRow, str(ids[1]))
        item = await session.scalar(
            select(QuoteRefreshItemRow).where(
                QuoteRefreshItemRow.run_id == str(result.run_id), QuoteRefreshItemRow.instrument_id == str(ids[1])
            )
        )
    assert quote is not None and quote.price == Decimal("42.10")
    assert item is not None and item.outcome == "stale"


async def test_all_failure_records_failed_run_and_idempotent_replay(database: Database) -> None:
    await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    yahoo = FakeProvider("yahoo", missing={"DEMO.US"})
    service = MarketDataService(database, registry(yahoo=yahoo), now=lambda: NOW)
    first = await service.refresh(RefreshScope.all(), "refresh-failed")
    replay = await service.refresh(RefreshScope.all(), "refresh-failed")
    assert first.status == "failed"
    assert replay == first
    assert yahoo.calls == [("DEMO.US",)]
    async with database.session() as session:
        run = await session.get(QuoteRefreshRunRow, str(first.run_id))
    assert run is not None and run.scope_json is None


async def test_concurrent_refresh_reports_existing_run(database: Database) -> None:
    await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    yahoo = FakeProvider("yahoo")
    yahoo.entered, yahoo.release = asyncio.Event(), asyncio.Event()
    service = MarketDataService(database, registry(yahoo=yahoo), now=lambda: NOW)
    active = asyncio.create_task(service.refresh(RefreshScope.all(), "refresh-active"))
    await asyncio.wait_for(yahoo.entered.wait(), timeout=1)
    with pytest.raises(RefreshInProgress) as raised:
        await service.refresh(RefreshScope.all(), "refresh-competing")
    assert raised.value.run_id is not None
    yahoo.release.set()
    await active


async def test_empty_scope_completes_without_provider_calls(database: Database) -> None:
    providers = registry()
    result = await MarketDataService(database, providers, now=lambda: NOW).refresh(RefreshScope.all(), "refresh-empty")
    assert (result.status, result.updated, result.stale, result.unavailable) == ("succeeded", 0, 0, 0)
    assert all(not provider.calls for provider in providers.providers)


def test_registry_rejects_non_reviewed_or_duplicate_providers() -> None:
    with pytest.raises(ValueError):
        ProviderRegistry((FakeProvider("eastmoney"), FakeProvider("custom"), FakeProvider("tencent")))
    with pytest.raises(ValueError):
        ProviderRegistry((FakeProvider("eastmoney"), FakeProvider("eastmoney"), FakeProvider("tencent")))


async def test_total_operation_timeout_finishes_with_sanitized_unavailable_item(database: Database) -> None:
    ids = await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    candidate_id, expired_idempotency, old_run_id = await seed_expired_retention(database, ids[0])
    yahoo = FakeProvider("yahoo")
    yahoo.entered, yahoo.release = asyncio.Event(), asyncio.Event()
    service = MarketDataService(
        database,
        registry(yahoo=yahoo),
        settings=Settings(market_operation_timeout_seconds=0.01),
        now=lambda: NOW,
    )
    result = await service.refresh(RefreshScope.all(), "refresh-timeout")
    assert result.status == "failed"
    async with database.session() as session:
        item = await session.get(QuoteRefreshItemRow, (str(result.run_id), str(ids[0])))
        run = await session.get(QuoteRefreshRunRow, str(result.run_id))
        expired_candidate = await session.get(InstrumentCandidateRow, candidate_id)
        expired_claim = await session.get(IdempotencyRow, expired_idempotency)
        expired_item = await session.get(QuoteRefreshItemRow, (old_run_id, str(ids[0])))
    assert item is not None
    assert (item.outcome, item.error_code) == ("unavailable", "REFRESH_TIMEOUT")
    assert run is not None and run.scope_json is None
    assert expired_candidate is None and expired_claim is None and expired_item is None


async def test_terminal_scope_is_not_retained_after_items_prune_while_latest_quote_keeps_run(
    database: Database,
) -> None:
    ids = await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    result = await MarketDataService(database, registry(), now=lambda: NOW).refresh(
        RefreshScope.all(), "refresh-privacy-prune"
    )
    async with database.session() as session, session.begin():
        await PortfolioRepository().prune_expired(session, NOW + timedelta(days=91))
    async with database.session() as session:
        run = await session.get(QuoteRefreshRunRow, str(result.run_id))
        quote = await session.get(LatestQuoteRow, str(ids[0]))
        item = await session.get(QuoteRefreshItemRow, (str(result.run_id), str(ids[0])))
    assert run is not None and run.scope_json is None
    assert quote is not None and quote.refresh_run_id == str(result.run_id)
    assert item is None


class ConcurrencyTracker:
    active = 0
    maximum = 0


class MeasuringProvider(FakeProvider):
    def __init__(self, name: str, tracker: ConcurrencyTracker) -> None:
        super().__init__(name)
        self.tracker = tracker

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        self.tracker.active += 1
        self.tracker.maximum = max(self.tracker.maximum, self.tracker.active)
        try:
            await asyncio.sleep(0.01)
            return await super().fetch_quotes(instruments)
        finally:
            self.tracker.active -= 1


async def test_refresh_honors_configured_concurrency_bound(database: Database) -> None:
    await seed_active(
        database,
        [
            ("600000.SH", Market.CN_SH, Currency.CNY, {"eastmoney": "1.600000"}),
            ("499991.BJ", Market.CN_BJ, Currency.CNY, {"eastmoney": "0.499991"}),
            ("00700.HK", Market.HK, Currency.HKD, {"yahoo": "0700.HK", "eastmoney": "116.00700"}),
            ("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"}),
        ],
    )
    tracker = ConcurrencyTracker()
    eastmoney = MeasuringProvider("eastmoney", tracker)
    yahoo = MeasuringProvider("yahoo", tracker)
    tencent = MeasuringProvider("tencent", tracker)
    service = MarketDataService(
        database,
        registry(east=eastmoney, yahoo=yahoo, tencent=tencent),
        settings=Settings(market_max_concurrency=2),
        now=lambda: NOW,
    )
    result = await service.refresh(RefreshScope.all(), "refresh-concurrency")
    assert result.updated == 4
    assert tracker.maximum == 2


class FailingFinalRepository(PortfolioRepository):
    async def complete_refresh(self, *args: object, **kwargs: object) -> None:
        await super().complete_refresh(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("synthetic transaction failure")


async def test_final_transaction_failure_rolls_back_quotes_and_startup_audit_fails_run(database: Database) -> None:
    ids = await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    prior_run = str(uuid4())
    async with database.session() as session, session.begin():
        session.add(
            QuoteRefreshRunRow(
                id=prior_run,
                scope_hash="1" * 64,
                status="completed",
                started_at=NOW - timedelta(days=1),
                finished_at=NOW - timedelta(days=1),
                updated_count=1,
                stale_count=0,
                unavailable_count=0,
            )
        )
        await session.flush()
        session.add(
            LatestQuoteRow(
                instrument_id=str(ids[0]),
                price=Decimal("42.10"),
                currency="USD",
                provider="yahoo",
                provider_symbol="DEMO",
                as_of=NOW - timedelta(days=1),
                fetched_at=NOW - timedelta(days=1),
                refresh_run_id=prior_run,
            )
        )
    service = MarketDataService(
        database,
        registry(),
        repository=FailingFinalRepository(),
        now=lambda: NOW,
    )
    with pytest.raises(RuntimeError, match="synthetic transaction failure"):
        await service.refresh(RefreshScope.all(), "refresh-atomic")
    async with database.session() as session:
        quote = await session.get(LatestQuoteRow, str(ids[0]))
        running = await session.scalar(select(QuoteRefreshRunRow).where(QuoteRefreshRunRow.status == "running"))
    assert quote is not None and quote.price == Decimal("42.10") and quote.refresh_run_id == prior_run
    assert running is not None
    observer = MarketDataService(
        database,
        registry(),
        now=lambda: NOW + timedelta(seconds=91),
    )
    await observer.startup()
    async with database.session() as session:
        audited = await session.get(QuoteRefreshRunRow, running.id)
    assert audited is not None and audited.status == "failed"
    assert audited.scope_json is None


class SlowSelectionRepository(PortfolioRepository):
    async def active_refresh_instruments(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.05)
        return await super().active_refresh_instruments(*args, **kwargs)  # type: ignore[arg-type]


class SlowFinalRepository(PortfolioRepository):
    async def complete_refresh(self, *args: object, **kwargs: object) -> None:
        await asyncio.sleep(0.05)
        await super().complete_refresh(*args, **kwargs)  # type: ignore[arg-type]


class CommitBeforeReturnRepository(PortfolioRepository):
    def __init__(self) -> None:
        self.committed = asyncio.Event()
        self.release = asyncio.Event()

    async def link_refresh_idempotency(self, session, claim, run_id):  # type: ignore[no-untyped-def]
        await super().link_refresh_idempotency(session, claim, run_id)
        await session.commit()
        self.committed.set()
        await self.release.wait()


async def test_whole_operation_deadline_covers_selection_without_durable_residue(database: Database) -> None:
    await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    service = MarketDataService(
        database,
        registry(),
        repository=SlowSelectionRepository(),
        settings=Settings(market_operation_timeout_seconds=0.01),
        now=lambda: NOW,
    )
    with pytest.raises(RefreshOperationTimeout):
        await service.refresh(RefreshScope.all(), "refresh-selection-timeout")
    async with database.session() as session:
        assert await session.scalar(select(QuoteRefreshRunRow)) is None
        assert await session.scalar(select(IdempotencyRow)) is None


async def test_whole_operation_deadline_terminalizes_an_admitted_final_write_timeout(
    database: Database,
) -> None:
    ids = await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    service = MarketDataService(
        database,
        registry(),
        repository=SlowFinalRepository(),
        settings=Settings(market_operation_timeout_seconds=0.01),
        now=lambda: NOW,
    )
    result = await service.refresh(RefreshScope.all(), "refresh-final-timeout")
    assert result.status == "failed"
    details = await service.refresh_run(result.run_id)
    assert details.run.terminal_error == "REFRESH_TIMEOUT"
    assert [(item.instrument_id, item.error_code) for item in details.items] == [
        (str(ids[0]), "REFRESH_TIMEOUT")
    ]
    replay = await service.refresh(RefreshScope.all(), "refresh-final-timeout")
    assert replay == result


async def test_timeout_after_admission_commit_terminalizes_without_provider_work(database: Database) -> None:
    await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    repository = CommitBeforeReturnRepository()
    yahoo = FakeProvider("yahoo")
    service = MarketDataService(
        database,
        registry(yahoo=yahoo),
        repository=repository,
        settings=Settings(market_operation_timeout_seconds=0.01),
        now=lambda: NOW,
    )

    async def release_commit() -> None:
        await repository.committed.wait()
        await asyncio.sleep(0.03)
        repository.release.set()

    releaser = asyncio.create_task(release_commit())
    result = await service.refresh(RefreshScope.all(), "refresh-admission-timeout")
    await releaser
    assert result.status == "failed"
    assert yahoo.calls == []
    details = await service.refresh_run(result.run_id)
    assert details.run.terminal_error == "REFRESH_TIMEOUT"
    assert details.run.scope_json is None
    assert await service.refresh(RefreshScope.all(), "refresh-admission-timeout") == result
    async with database.session() as session:
        pending = await session.scalar(select(IdempotencyRow).where(IdempotencyRow.state == "pending"))
        running = await session.scalar(select(QuoteRefreshRunRow).where(QuoteRefreshRunRow.status == "running"))
    assert pending is None and running is None


async def test_cancellation_after_admission_commit_preserves_cancellation_and_exact_replay(
    database: Database,
) -> None:
    await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    repository = CommitBeforeReturnRepository()
    yahoo = FakeProvider("yahoo")
    service = MarketDataService(database, registry(yahoo=yahoo), repository=repository, now=lambda: NOW)
    active = asyncio.create_task(service.refresh(RefreshScope.all(), "refresh-admission-cancel"))
    await repository.committed.wait()
    active.cancel("original admission cancellation")
    await asyncio.sleep(0)
    try:
        assert not active.done()
    finally:
        repository.release.set()
    with pytest.raises(asyncio.CancelledError, match="original admission cancellation"):
        await active
    assert yahoo.calls == []
    async with database.session() as session:
        run = await session.scalar(select(QuoteRefreshRunRow))
        pending = await session.scalar(select(IdempotencyRow).where(IdempotencyRow.state == "pending"))
    assert run is not None and run.status == "failed" and run.terminal_error == "REFRESH_CANCELLED"
    assert run.scope_json is None and pending is None
    replay = await service.refresh(RefreshScope.all(), "refresh-admission-cancel")
    assert replay.run_id == UUID(run.id) and replay.status == "failed"


class AdmissionBarrier:
    def __init__(self) -> None:
        self.count = 0
        self.ready = asyncio.Event()


class BarrierRepository(PortfolioRepository):
    def __init__(self, barrier: AdmissionBarrier) -> None:
        self.barrier = barrier

    async def active_refresh_instruments(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        records = await super().active_refresh_instruments(*args, **kwargs)  # type: ignore[arg-type]
        self.barrier.count += 1
        if self.barrier.count == 2:
            self.barrier.ready.set()
        await asyncio.wait_for(self.barrier.ready.wait(), timeout=1)
        return records


class ProviderCounter:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()


class SharedBlockingProvider(FakeProvider):
    def __init__(self, name: str, counter: ProviderCounter) -> None:
        super().__init__(name)
        self.counter = counter

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        self.counter.calls += 1
        self.counter.entered.set()
        await self.counter.release.wait()
        return await super().fetch_quotes(instruments)


async def test_two_services_use_one_database_owned_run_and_loser_gets_in_progress(database: Database) -> None:
    await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    second_database = Database(database.path)
    await second_database.start()
    barrier = AdmissionBarrier()
    counter = ProviderCounter()
    first = MarketDataService(
        database,
        registry(yahoo=SharedBlockingProvider("yahoo", counter)),
        repository=BarrierRepository(barrier),
        now=lambda: NOW,
    )
    second = MarketDataService(
        second_database,
        registry(yahoo=SharedBlockingProvider("yahoo", counter)),
        repository=BarrierRepository(barrier),
        now=lambda: NOW,
    )
    tasks = {
        asyncio.create_task(first.refresh(RefreshScope.all(), "refresh-owner-a")),
        asyncio.create_task(second.refresh(RefreshScope.all(), "refresh-owner-b")),
    }
    try:
        await asyncio.wait_for(counter.entered.wait(), timeout=1)
        done, pending = await asyncio.wait(tasks, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == 1
        loser = done.pop()
        assert isinstance(loser.exception(), RefreshInProgress)
        assert counter.calls == 1
        counter.release.set()
        winner = await asyncio.wait_for(pending.pop(), timeout=1)
        assert winner.status == "succeeded"
        async with database.session() as session:
            runs = list(await session.scalars(select(QuoteRefreshRunRow)))
        assert len(runs) == 1
        assert runs[0].owner_token is None
        assert runs[0].lease_expires_at is None
    finally:
        counter.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second_database.close()


async def test_second_service_startup_does_not_abandon_a_live_lease(database: Database) -> None:
    await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    yahoo = FakeProvider("yahoo")
    yahoo.entered, yahoo.release = asyncio.Event(), asyncio.Event()
    owner = MarketDataService(database, registry(yahoo=yahoo), now=lambda: NOW)
    observer = MarketDataService(database, registry(), now=lambda: NOW)
    active = asyncio.create_task(owner.refresh(RefreshScope.all(), "refresh-live-lease"))
    await asyncio.wait_for(yahoo.entered.wait(), timeout=1)
    await observer.startup()
    async with database.session() as session:
        run = await session.scalar(select(QuoteRefreshRunRow))
    assert run is not None and run.status == "running"
    assert run.lease_expires_at is not None and run.lease_expires_at > NOW
    yahoo.release.set()
    assert (await active).status == "succeeded"


async def test_expired_lease_recovery_terminalizes_items_and_idempotency_for_exact_replay(
    database: Database,
) -> None:
    ids = await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    run_id = str(uuid4())
    request_hash = __import__("hashlib").sha256(b"null").hexdigest()
    async with database.session() as session, session.begin():
        session.add(
            QuoteRefreshRunRow(
                id=run_id,
                scope_hash="2" * 64,
                status="running",
                started_at=NOW - timedelta(minutes=2),
                finished_at=None,
                updated_count=0,
                stale_count=0,
                unavailable_count=0,
                owner_token=str(uuid4()),
                lease_expires_at=NOW - timedelta(seconds=1),
                scope_json=f'["{ids[0]}"]',
                terminal_error=None,
            )
        )
        session.add(
            IdempotencyRow(
                scope="market-data:refresh",
                key_hash=hash_idempotency_key("refresh-crashed"),
                request_hash=request_hash,
                state="pending",
                resource_id=run_id,
                resource_version=None,
                response_status=None,
                created_at=NOW - timedelta(minutes=2),
                expires_at=NOW + timedelta(hours=1),
            )
        )
    service = MarketDataService(database, registry(), now=lambda: NOW)
    await service.startup()
    details = await service.refresh_run(UUID(run_id))
    assert details.run.status == "failed"
    assert details.run.terminal_error == "REFRESH_ABANDONED"
    assert details.run.scope_json is None
    assert [(item.instrument_id, item.outcome, item.error_code) for item in details.items] == [
        (str(ids[0]), "unavailable", "REFRESH_ABANDONED")
    ]
    replay = await service.refresh(RefreshScope.all(), "refresh-crashed")
    assert replay.run_id == UUID(run_id)
    assert replay.status == "failed"


async def test_external_cancellation_terminalizes_run_items_and_idempotency_before_propagating(
    database: Database,
) -> None:
    ids = await seed_active(database, [("DEMO.US", Market.US, Currency.USD, {"yahoo": "DEMO"})])
    candidate_id, expired_idempotency, old_run_id = await seed_expired_retention(database, ids[0])
    yahoo = FakeProvider("yahoo")
    yahoo.entered, yahoo.release = asyncio.Event(), asyncio.Event()
    service = MarketDataService(database, registry(yahoo=yahoo), now=lambda: NOW)
    active = asyncio.create_task(service.refresh(RefreshScope.all(), "refresh-cancelled"))
    await asyncio.wait_for(yahoo.entered.wait(), timeout=1)
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    async with database.session() as session:
        run = await session.scalar(
            select(QuoteRefreshRunRow).where(QuoteRefreshRunRow.terminal_error == "REFRESH_CANCELLED")
        )
        pending = await session.scalar(
            select(IdempotencyRow).where(IdempotencyRow.state == "pending")
        )
    assert run is not None and run.status == "failed" and run.terminal_error == "REFRESH_CANCELLED"
    assert run.scope_json is None
    assert run.finished_at == NOW
    assert pending is None
    details = await service.refresh_run(UUID(run.id))
    assert [(item.instrument_id, item.error_code) for item in details.items] == [
        (str(ids[0]), "REFRESH_CANCELLED")
    ]
    replay = await service.refresh(RefreshScope.all(), "refresh-cancelled")
    assert replay.run_id == UUID(run.id) and replay.status == "failed"
    assert yahoo.calls == [("DEMO.US",)]
    async with database.session() as session:
        assert await session.get(InstrumentCandidateRow, candidate_id) is None
        assert await session.get(IdempotencyRow, expired_idempotency) is None
        assert await session.get(QuoteRefreshItemRow, (old_run_id, str(ids[0]))) is None
