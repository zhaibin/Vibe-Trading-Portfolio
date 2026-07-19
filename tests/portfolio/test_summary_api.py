import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text

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

CNY_ACCOUNT_ID = "10000000-0000-4000-8000-000000000001"
HKD_ACCOUNT_ID = "10000000-0000-4000-8000-000000000002"
USD_ACCOUNT_ID = "10000000-0000-4000-8000-000000000003"
ARCHIVED_ACCOUNT_ID = "10000000-0000-4000-8000-000000000004"
CNY_INSTRUMENT_ID = "20000000-0000-4000-8000-000000000001"
ARCHIVED_INSTRUMENT_ID = "20000000-0000-4000-8000-000000000002"
CNY_POSITION_ID = "30000000-0000-4000-8000-000000000001"
ARCHIVED_POSITION_ID = "30000000-0000-4000-8000-000000000002"
CNY_RUN_ID = "40000000-0000-4000-8000-000000000001"


def account_row(
    account_id: str,
    *,
    name: str,
    currency: str,
    cash: str,
    now: datetime,
    archived: bool = False,
) -> AccountRow:
    return AccountRow(
        id=account_id,
        name=name,
        normalized_name=name,
        currency=currency,
        cash_balance=Decimal(cash),
        version=1,
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=1),
        archived_at=now - timedelta(hours=12) if archived else None,
    )


def instrument_row(
    instrument_id: str,
    *,
    symbol: str,
    market: str,
    currency: str,
    now: datetime,
) -> InstrumentRow:
    return InstrumentRow(
        id=instrument_id,
        canonical_symbol=symbol,
        name=f"Instrument {symbol}",
        market=market,
        currency=currency,
        asset_type="equity",
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=1),
    )


def position_row(
    position_id: str,
    *,
    account_id: str,
    instrument_id: str,
    quantity: str,
    average_cost: str,
    now: datetime,
    archived: bool = False,
) -> PositionRow:
    return PositionRow(
        id=position_id,
        account_id=account_id,
        instrument_id=instrument_id,
        quantity=Decimal(quantity),
        average_cost=Decimal(average_cost),
        note=None,
        version=1,
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=1),
        archived_at=now - timedelta(hours=12) if archived else None,
    )


def refresh_run(
    run_id: str,
    *,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
) -> QuoteRefreshRunRow:
    return QuoteRefreshRunRow(
        id=run_id,
        scope_hash=run_id.replace("-", "")[:32].ljust(64, "a"),
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        updated_count=1 if status == "completed" else 0,
        stale_count=1 if status in {"partial", "failed"} else 0,
        unavailable_count=1 if status == "failed" else 0,
    )


def latest_quote(
    instrument_id: str,
    *,
    run_id: str,
    price: str,
    currency: str,
    now: datetime,
) -> LatestQuoteRow:
    return LatestQuoteRow(
        instrument_id=instrument_id,
        price=Decimal(price),
        currency=currency,
        provider="fake",
        provider_symbol=instrument_id,
        as_of=now - timedelta(hours=1),
        fetched_at=now - timedelta(minutes=59),
        refresh_run_id=run_id,
    )


def refresh_item(
    run_id: str,
    instrument_id: str,
    *,
    outcome: str,
    created_at: datetime,
) -> QuoteRefreshItemRow:
    return QuoteRefreshItemRow(
        run_id=run_id,
        instrument_id=instrument_id,
        outcome=outcome,
        provider="fake" if outcome == "updated" else None,
        error_code=None if outcome == "updated" else "QUOTE_UNAVAILABLE",
        created_at=created_at,
    )


@pytest_asyncio.fixture
async def database(tmp_path: pytest.TempPathFactory) -> AsyncIterator[Database]:
    database = Database(tmp_path / "portfolio.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


@asynccontextmanager
async def client_for(database: Database) -> AsyncIterator[tuple[httpx.AsyncClient, PortfolioService]]:
    service = PortfolioService(database)
    app = FastAPI()
    app.include_router(build_portfolio_router(service))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        yield client, service


@pytest_asyncio.fixture
async def summary_client(database: Database) -> AsyncIterator[tuple[httpx.AsyncClient, PortfolioService]]:
    now = datetime.now(UTC)
    foundations = [
        account_row(CNY_ACCOUNT_ID, name="人民币账户", currency="CNY", cash="100", now=now),
        account_row(HKD_ACCOUNT_ID, name="港币账户", currency="HKD", cash="200", now=now),
        account_row(USD_ACCOUNT_ID, name="美元账户", currency="USD", cash="300", now=now),
        account_row(
            ARCHIVED_ACCOUNT_ID,
            name="已归档账户",
            currency="CNY",
            cash="999",
            now=now,
            archived=True,
        ),
        instrument_row(CNY_INSTRUMENT_ID, symbol="600519.SH", market="CN_SH", currency="CNY", now=now),
        instrument_row(
            ARCHIVED_INSTRUMENT_ID,
            symbol="000001.SZ",
            market="CN_SZ",
            currency="CNY",
            now=now,
        ),
        refresh_run(
            CNY_RUN_ID,
            status="completed",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(minutes=59),
        ),
    ]
    dependents = [
        position_row(
            CNY_POSITION_ID,
            account_id=CNY_ACCOUNT_ID,
            instrument_id=CNY_INSTRUMENT_ID,
            quantity="2",
            average_cost="5",
            now=now,
        ),
        position_row(
            ARCHIVED_POSITION_ID,
            account_id=CNY_ACCOUNT_ID,
            instrument_id=ARCHIVED_INSTRUMENT_ID,
            quantity="100",
            average_cost="100",
            now=now,
            archived=True,
        ),
        latest_quote(CNY_INSTRUMENT_ID, run_id=CNY_RUN_ID, price="10", currency="CNY", now=now),
        refresh_item(
            CNY_RUN_ID,
            CNY_INSTRUMENT_ID,
            outcome="updated",
            created_at=now - timedelta(minutes=59),
        ),
    ]
    async with database.session() as session, session.begin():
        session.add_all(foundations)
        await session.flush()
        session.add_all(dependents)

    async with client_for(database) as client_and_service:
        yield client_and_service


async def test_summary_endpoint_is_currency_local_exact_archived_safe_and_has_no_io(
    summary_client: tuple[httpx.AsyncClient, PortfolioService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service = summary_client
    network_calls: list[tuple[object, ...]] = []

    def block_network(*args: object, **kwargs: object) -> None:
        del kwargs
        network_calls.append(args)
        raise AssertionError("summary reads must not open a network socket")

    monkeypatch.setattr(socket.socket, "connect", block_network)
    monkeypatch.setattr(socket, "create_connection", block_network)

    assert set(vars(service)) == {"database", "repository"}
    cny = await client.get("/api/v1/portfolio/summary?currency=CNY")
    hkd = await client.get("/api/v1/portfolio/summary?currency=HKD")
    usd = await client.get("/api/v1/portfolio/summary?currency=USD")

    assert cny.status_code == hkd.status_code == usd.status_code == 200
    assert cny.json()["currency"] == "CNY"
    assert cny.json()["account_count"] == 1
    assert cny.json()["position_count"] == 1
    assert cny.json()["market_value"] == "20.000000"
    assert cny.json()["position_cost"] == "10.000000"
    assert cny.json()["unrealized_pnl"] == "10.000000"
    assert cny.json()["unrealized_pnl_pct"] == "1.000000"
    assert cny.json()["known_cash"] == "100.000000"
    assert cny.json()["total_value"] == "120.000000"
    assert cny.json()["positions"][0]["quote_state"] == "fresh"
    assert cny.json()["positions"][0]["quote_as_of"].endswith("Z")
    assert cny.json()["positions"][0]["quote_fetched_at"].endswith("Z")

    assert hkd.json()["currency"] == "HKD"
    assert hkd.json()["known_cash"] == "200.000000"
    assert hkd.json()["total_value"] == "200.000000"
    assert hkd.json()["positions"] == []

    assert usd.json()["currency"] == "USD"
    assert usd.json()["known_cash"] == "300.000000"
    assert usd.json()["total_value"] == "300.000000"
    assert usd.json()["positions"] == []
    assert network_calls == []


async def test_summary_uses_the_latest_terminal_attempt_with_deterministic_timestamp_ordering(
    database: Database,
) -> None:
    now = datetime.now(UTC)
    account_id = "50000000-0000-4000-8000-000000000001"
    running_instrument_id = "60000000-0000-4000-8000-000000000001"
    tied_instrument_id = "60000000-0000-4000-8000-000000000002"
    completed_run_id = "70000000-0000-4000-8000-000000000001"
    running_run_id = "70000000-0000-4000-8000-000000000002"
    tied_updated_run_id = "f0000000-0000-4000-8000-000000000001"
    tied_failed_run_id = "10000000-0000-4000-8000-000000000001"
    tied_created_at = now - timedelta(hours=2)
    foundations = [
        account_row(account_id, name="刷新账户", currency="CNY", cash="0", now=now),
        instrument_row(
            running_instrument_id,
            symbol="600000.SH",
            market="CN_SH",
            currency="CNY",
            now=now,
        ),
        instrument_row(
            tied_instrument_id,
            symbol="600001.SH",
            market="CN_SH",
            currency="CNY",
            now=now,
        ),
        refresh_run(
            completed_run_id,
            status="completed",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3),
        ),
        refresh_run(
            running_run_id,
            status="running",
            started_at=now - timedelta(minutes=30),
            finished_at=None,
        ),
        refresh_run(
            tied_updated_run_id,
            status="completed",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3),
        ),
        refresh_run(
            tied_failed_run_id,
            status="failed",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=1),
        ),
    ]
    dependents = [
        position_row(
            "80000000-0000-4000-8000-000000000001",
            account_id=account_id,
            instrument_id=running_instrument_id,
            quantity="1",
            average_cost="5",
            now=now,
        ),
        position_row(
            "80000000-0000-4000-8000-000000000002",
            account_id=account_id,
            instrument_id=tied_instrument_id,
            quantity="1",
            average_cost="5",
            now=now,
        ),
        latest_quote(running_instrument_id, run_id=completed_run_id, price="10", currency="CNY", now=now),
        latest_quote(tied_instrument_id, run_id=tied_updated_run_id, price="10", currency="CNY", now=now),
        refresh_item(
            completed_run_id,
            running_instrument_id,
            outcome="updated",
            created_at=now - timedelta(hours=3),
        ),
        refresh_item(
            running_run_id,
            running_instrument_id,
            outcome="unavailable",
            created_at=now - timedelta(minutes=29),
        ),
        refresh_item(
            tied_updated_run_id,
            tied_instrument_id,
            outcome="updated",
            created_at=tied_created_at,
        ),
        refresh_item(
            tied_failed_run_id,
            tied_instrument_id,
            outcome="unavailable",
            created_at=tied_created_at,
        ),
    ]
    async with database.session() as session, session.begin():
        session.add_all(foundations)
        await session.flush()
        session.add_all(dependents)

    async with client_for(database) as (client, _):
        response = await client.get("/api/v1/portfolio/summary?currency=CNY")

    assert response.status_code == 200
    positions = {item["instrument_id"]: item for item in response.json()["positions"]}
    assert positions[running_instrument_id]["quote_state"] == "fresh"
    assert positions[tied_instrument_id]["quote_state"] == "stale"
    assert response.json()["stale_count"] == 1
    assert response.json()["estimated"] is True


@pytest.mark.parametrize(
    ("column", "stored_value"),
    [("as_of", "2026-07-19T12:00:00"), ("fetched_at", "not-a-timestamp")],
)
async def test_summary_endpoint_fails_closed_on_corrupt_stored_quote_timestamps(
    summary_client: tuple[httpx.AsyncClient, PortfolioService],
    database: Database,
    column: str,
    stored_value: str,
) -> None:
    client, _ = summary_client
    statement = (
        text("UPDATE latest_quotes SET as_of = :value WHERE instrument_id = :instrument_id")
        if column == "as_of"
        else text("UPDATE latest_quotes SET fetched_at = :value WHERE instrument_id = :instrument_id")
    )
    async with database.session() as session, session.begin():
        await session.execute(statement, {"value": stored_value, "instrument_id": CNY_INSTRUMENT_ID})

    response = await client.get("/api/v1/portfolio/summary?currency=CNY")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "PORTFOLIO_UNAVAILABLE"}}
    assert stored_value not in response.text


async def test_summary_endpoint_rejects_unknown_currency_with_stable_error(
    summary_client: tuple[httpx.AsyncClient, PortfolioService],
) -> None:
    client, _ = summary_client

    response = await client.get("/api/v1/portfolio/summary?currency=EUR")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
