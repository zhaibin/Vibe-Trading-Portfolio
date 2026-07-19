from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import (
    AccountRow,
    InstrumentRow,
    LatestQuoteRow,
    PositionRow,
    QuoteRefreshItemRow,
    QuoteRefreshRunRow,
)


class FakeMarketProvider:
    def __init__(self) -> None:
        self.calls = 0


@pytest_asyncio.fixture
async def database(tmp_path: pytest.TempPathFactory) -> AsyncIterator[Database]:
    database = Database(tmp_path / "portfolio.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def summary_client(database: Database) -> AsyncIterator[tuple[httpx.AsyncClient, FakeMarketProvider]]:
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add_all(
            [
                AccountRow(
                    id="10000000-0000-4000-8000-000000000001",
                    name="人民币账户",
                    normalized_name="人民币账户",
                    currency="CNY",
                    cash_balance=Decimal("100"),
                    version=1,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=1),
                    archived_at=None,
                ),
                AccountRow(
                    id="10000000-0000-4000-8000-000000000002",
                    name="港币账户",
                    normalized_name="港币账户",
                    currency="HKD",
                    cash_balance=Decimal("200"),
                    version=1,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=1),
                    archived_at=None,
                ),
                InstrumentRow(
                    id="20000000-0000-4000-8000-000000000001",
                    canonical_symbol="600519.SH",
                    name="测试证券",
                    market="CN_SH",
                    currency="CNY",
                    asset_type="equity",
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=1),
                ),
                PositionRow(
                    id="30000000-0000-4000-8000-000000000001",
                    account_id="10000000-0000-4000-8000-000000000001",
                    instrument_id="20000000-0000-4000-8000-000000000001",
                    quantity=Decimal("2"),
                    average_cost=Decimal("5"),
                    note=None,
                    version=1,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=1),
                    archived_at=None,
                ),
                QuoteRefreshRunRow(
                    id="40000000-0000-4000-8000-000000000001",
                    scope_hash="a" * 64,
                    status="completed",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(minutes=59),
                    updated_count=1,
                    stale_count=0,
                    unavailable_count=0,
                ),
                LatestQuoteRow(
                    instrument_id="20000000-0000-4000-8000-000000000001",
                    price=Decimal("10"),
                    currency="CNY",
                    provider="fake",
                    provider_symbol="600519",
                    as_of=now - timedelta(hours=1),
                    fetched_at=now - timedelta(minutes=59),
                    refresh_run_id="40000000-0000-4000-8000-000000000001",
                ),
                QuoteRefreshItemRow(
                    run_id="40000000-0000-4000-8000-000000000001",
                    instrument_id="20000000-0000-4000-8000-000000000001",
                    outcome="updated",
                    provider="fake",
                    error_code=None,
                    created_at=now - timedelta(minutes=59),
                ),
            ]
        )
        await session.flush(
            [
                row
                for row in session.new
                if isinstance(row, (AccountRow, InstrumentRow, QuoteRefreshRunRow))
            ]
        )

    provider = FakeMarketProvider()
    app = FastAPI()
    app.state.market_provider = provider
    app.include_router(build_portfolio_router(PortfolioService(database)))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        yield client, provider


async def test_summary_endpoint_is_currency_local_exact_and_sqlite_only(
    summary_client: tuple[httpx.AsyncClient, FakeMarketProvider],
) -> None:
    client, provider = summary_client

    cny = await client.get("/api/v1/portfolio/summary?currency=CNY")
    hkd = await client.get("/api/v1/portfolio/summary?currency=HKD")

    assert cny.status_code == 200
    assert cny.json()["currency"] == "CNY"
    assert cny.json()["market_value"] == "20.000000"
    assert cny.json()["position_cost"] == "10.000000"
    assert cny.json()["unrealized_pnl"] == "10.000000"
    assert cny.json()["unrealized_pnl_pct"] == "1.000000"
    assert cny.json()["known_cash"] == "100.000000"
    assert cny.json()["total_value"] == "120.000000"
    assert cny.json()["positions"][0]["quote_state"] == "fresh"
    assert cny.json()["positions"][0]["quote_as_of"].endswith("Z")
    assert cny.json()["positions"][0]["quote_fetched_at"].endswith("Z")

    assert hkd.status_code == 200
    assert hkd.json()["currency"] == "HKD"
    assert hkd.json()["market_value"] == "0.000000"
    assert hkd.json()["known_cash"] == "200.000000"
    assert hkd.json()["total_value"] == "200.000000"
    assert hkd.json()["positions"] == []
    assert provider.calls == 0


async def test_summary_endpoint_rejects_unknown_currency_with_stable_error(
    summary_client: tuple[httpx.AsyncClient, FakeMarketProvider],
) -> None:
    client, provider = summary_client

    response = await client.get("/api/v1/portfolio/summary?currency=EUR")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert provider.calls == 0
