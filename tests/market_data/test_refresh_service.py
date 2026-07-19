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
from vibe_portfolio.market_data.service import MarketDataService, ProviderRegistry, RefreshInProgress
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.repository import PortfolioRepository
from vibe_portfolio.portfolio.tables import (
    AccountRow,
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
    assert item is not None
    assert (item.outcome, item.error_code) == ("unavailable", "PROVIDER_TIMEOUT")


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
    await service.startup()
    async with database.session() as session:
        audited = await session.get(QuoteRefreshRunRow, running.id)
    assert audited is not None and audited.status == "failed"
