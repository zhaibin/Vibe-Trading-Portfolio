from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import select

from vibe_portfolio.market_data.models import (
    InstrumentCandidate,
    ProviderInstrument,
    ProviderQuote,
    ProviderSymbol,
)
from vibe_portfolio.market_data.service import MarketDataService, MarketSearchUnavailable, SearchValidationError
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.tables import InstrumentCandidateRow


class FakeProvider:
    def __init__(self, name: str, candidates: list[InstrumentCandidate] | None = None, *, fails: bool = False) -> None:
        self.name = name
        self.candidates = candidates or []
        self.fails = fails
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        self.calls.append((query, limit))
        if self.fails:
            raise RuntimeError("synthetic provider failure")
        return self.candidates

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
    return InstrumentCandidate(
        canonical_symbol=symbol,
        name=name,
        market=market,
        currency=currency,
        asset_type=asset_type,
        provider_symbols=(ProviderSymbol(provider, symbol.removesuffix(".US")),),
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
