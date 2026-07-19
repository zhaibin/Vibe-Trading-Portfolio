from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from vibe_portfolio.market_data.models import InstrumentCandidate, ProviderInstrument, ProviderQuote
from vibe_portfolio.market_data.router import build_market_data_router
from vibe_portfolio.market_data.service import MarketDataService, ProviderRegistry
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import AccountRow, InstrumentProviderSymbolRow, InstrumentRow, PositionRow

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


class ApiProvider:
    def __init__(self, name: str, *, missing: bool = False) -> None:
        self.name = name
        self.missing = missing
        self.calls = 0

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        return []

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        self.calls += 1
        if self.missing:
            return []
        instrument = instruments[0]
        return [
            ProviderQuote(
                instrument.canonical_symbol,
                instrument.provider_symbol,
                Decimal("19.125001"),
                instrument.currency,
                NOW - timedelta(minutes=1),
                self.name,
            )
        ]


@pytest_asyncio.fixture
async def database(tmp_path: pytest.TempPathFactory) -> AsyncIterator[Database]:
    database = Database(tmp_path / "refresh-api.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


async def seed_us(database: Database) -> str:
    account_id, instrument_id, position_id = str(uuid4()), str(uuid4()), str(uuid4())
    async with database.session() as session, session.begin():
        session.add(
            AccountRow(
                id=account_id,
                name="USD",
                normalized_name="usd",
                currency="USD",
                cash_balance=Decimal("0"),
                version=1,
                created_at=NOW,
                updated_at=NOW,
                archived_at=None,
            )
        )
        session.add(
            InstrumentRow(
                id=instrument_id,
                canonical_symbol="DEMO.US",
                name="Demo",
                market="US",
                currency="USD",
                asset_type="equity",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(InstrumentProviderSymbolRow(instrument_id=instrument_id, provider="yahoo", provider_symbol="DEMO"))
        session.add(
            PositionRow(
                id=position_id,
                account_id=account_id,
                instrument_id=instrument_id,
                quantity=Decimal("1"),
                average_cost=Decimal("10"),
                note=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                archived_at=None,
            )
        )
    return instrument_id


def app_for(database: Database, yahoo: ApiProvider) -> tuple[FastAPI, tuple[ApiProvider, ApiProvider, ApiProvider]]:
    east, tencent = ApiProvider("eastmoney"), ApiProvider("tencent")
    providers = (east, yahoo, tencent)
    service = MarketDataService(database, ProviderRegistry(providers), now=lambda: NOW)
    app = FastAPI()
    app.include_router(build_market_data_router(service))
    app.include_router(build_portfolio_router(PortfolioService(database)))
    return app, providers


async def request(app: FastAPI, method: str, path: str, **kwargs: object) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://sidecar"
    ) as client:
        return await client.request(method, path, **kwargs)


async def test_post_refresh_and_get_run_return_sanitized_outcomes(database: Database) -> None:
    instrument_id = await seed_us(database)
    app, _ = app_for(database, ApiProvider("yahoo"))
    response = await request(
        app,
        "POST",
        "/api/v1/market-data/refresh",
        json={"instrument_ids": [instrument_id]},
        headers={"Idempotency-Key": "refresh-api-1"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["updated"] == 1
    assert response.json()["items"] == [
        {"instrument_id": instrument_id, "outcome": "updated", "provider": "yahoo", "error_code": None}
    ]
    fetched = await request(app, "GET", f"/api/v1/market-data/refresh/{response.json()['run_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == response.json()


async def test_all_failed_refresh_returns_502_with_sanitized_run(database: Database) -> None:
    await seed_us(database)
    app, _ = app_for(database, ApiProvider("yahoo", missing=True))
    response = await request(
        app, "POST", "/api/v1/market-data/refresh", json={}, headers={"Idempotency-Key": "refresh-api-failed"}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "QUOTE_UNAVAILABLE"
    assert response.json()["error"]["fields"]["run"]["status"] == "failed"
    run_id = response.json()["error"]["fields"]["run"]["run_id"]
    assert (await request(app, "GET", f"/api/v1/market-data/refresh/{run_id}")).json()["status"] == "failed"


async def test_refresh_api_rejects_missing_key_duplicate_or_inactive_scope(database: Database) -> None:
    instrument_id = await seed_us(database)
    app, _ = app_for(database, ApiProvider("yahoo"))
    missing_key = await request(app, "POST", "/api/v1/market-data/refresh", json={})
    duplicate = await request(
        app,
        "POST",
        "/api/v1/market-data/refresh",
        json={"instrument_ids": [instrument_id, instrument_id]},
        headers={"Idempotency-Key": "refresh-api-duplicate"},
    )
    inactive = await request(
        app,
        "POST",
        "/api/v1/market-data/refresh",
        json={"instrument_ids": [str(uuid4())]},
        headers={"Idempotency-Key": "refresh-api-inactive"},
    )
    assert missing_key.status_code == duplicate.status_code == inactive.status_code == 422
    assert {item.json()["error"]["code"] for item in (missing_key, duplicate, inactive)} == {"VALIDATION_ERROR"}


async def test_dashboard_get_never_triggers_quote_refresh(database: Database) -> None:
    await seed_us(database)
    yahoo = ApiProvider("yahoo")
    app, providers = app_for(database, yahoo)
    response = await request(app, "GET", "/api/v1/portfolio/summary?currency=USD")
    assert response.status_code == 200
    assert all(provider.calls == 0 for provider in providers)
