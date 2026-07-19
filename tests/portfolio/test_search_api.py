from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from vibe_portfolio.market_data.models import (
    InstrumentCandidate,
    ProviderInstrument,
    ProviderQuote,
    ProviderSymbol,
)
from vibe_portfolio.market_data.router import build_market_data_router
from vibe_portfolio.market_data.service import MarketDataService
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import InstrumentCandidateRow, InstrumentProviderSymbolRow


class ApiProvider:
    def __init__(self, name: str, *, fails: bool = False, empty: bool = False) -> None:
        self.name = name
        self.fails = fails
        self.empty = empty

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        if self.fails:
            raise RuntimeError("synthetic provider failure")
        if self.empty:
            return []
        provider_symbol = "105.DEMO" if self.name == "eastmoney" else "DEMO"
        return [
            InstrumentCandidate(
                canonical_symbol="DEMO.US",
                name="Fictional Demo Systems",
                market=Market.US,
                currency=Currency.USD,
                asset_type=AssetType.EQUITY,
                provider_symbols=(ProviderSymbol(self.name, provider_symbol),),
            )
        ][:limit]

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        return []


@pytest_asyncio.fixture
async def database(tmp_path: pytest.TempPathFactory) -> AsyncIterator[Database]:
    database = Database(tmp_path / "search-api.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


def app_for(database: Database, providers: tuple[ApiProvider, ApiProvider], *, now: datetime | None = None) -> FastAPI:
    app = FastAPI()
    service = MarketDataService(database, providers, now=(lambda: now) if now else None)
    app.include_router(build_market_data_router(service))
    app.include_router(build_portfolio_router(PortfolioService(database)))
    return app


async def request(app: FastAPI, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        return await client.request(method, path, **kwargs)


async def test_search_returns_opaque_candidates_that_confirmation_consumes(database: Database) -> None:
    now = datetime.now(UTC)
    app = app_for(database, (ApiProvider("eastmoney"), ApiProvider("yahoo")), now=now)

    searched = await request(app, "GET", "/api/v1/instruments/search", params={"q": "demo", "limit": 5})

    assert searched.status_code == 200
    assert searched.json() == [
        {
            "candidate_id": searched.json()[0]["candidate_id"],
            "canonical_symbol": "DEMO.US",
            "name": "Fictional Demo Systems",
            "market": "US",
            "currency": "USD",
            "asset_type": "equity",
            "sources": ["eastmoney", "yahoo"],
        }
    ]
    candidate_id = searched.json()[0]["candidate_id"]
    async with database.session() as session:
        cached = await session.scalar(select(InstrumentCandidateRow).where(InstrumentCandidateRow.id == candidate_id))
    assert cached is not None
    assert cached.expires_at - cached.created_at == timedelta(minutes=15)

    confirmed = await request(
        app,
        "POST",
        "/api/v1/instruments/confirm",
        json={"candidate_id": candidate_id},
        headers={"Idempotency-Key": "confirm-search-result", "Origin": "http://127.0.0.1:8765"},
    )
    assert confirmed.status_code == 201
    async with database.session() as session:
        mappings = (await session.scalars(select(InstrumentProviderSymbolRow))).all()
    assert {(mapping.provider, mapping.provider_symbol) for mapping in mappings} == {
        ("eastmoney", "105.DEMO"),
        ("yahoo", "DEMO"),
    }


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"q": "", "limit": 5}, "q"),
        ({"q": "demo?", "limit": 5}, "q"),
        ({"q": "demo", "limit": 0}, "limit"),
        ({"q": "demo", "limit": 26}, "limit"),
    ],
)
async def test_search_api_returns_stable_validation_errors(
    database: Database, params: dict[str, object], field: str
) -> None:
    response = await request(
        app_for(database, (ApiProvider("eastmoney"), ApiProvider("yahoo"))),
        "GET",
        "/api/v1/instruments/search",
        params=params,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert any(field in key for key in response.json()["error"]["fields"])


async def test_search_api_distinguishes_total_failure_from_empty_success(database: Database) -> None:
    failed = await request(
        app_for(database, (ApiProvider("eastmoney", fails=True), ApiProvider("yahoo", fails=True))),
        "GET",
        "/api/v1/instruments/search?q=demo&limit=5",
    )
    assert failed.status_code == 503
    assert failed.json() == {"error": {"code": "MARKET_SEARCH_UNAVAILABLE"}}

    empty = await request(
        app_for(database, (ApiProvider("eastmoney", empty=True), ApiProvider("yahoo", empty=True))),
        "GET",
        "/api/v1/instruments/search?q=missing&limit=5",
    )
    assert empty.status_code == 200
    assert empty.json() == []


async def test_search_api_keeps_valid_partial_result_when_one_provider_fails(database: Database) -> None:
    response = await request(
        app_for(database, (ApiProvider("eastmoney", fails=True), ApiProvider("yahoo"))),
        "GET",
        "/api/v1/instruments/search?q=demo&limit=5",
    )
    assert response.status_code == 200
    assert response.json()[0]["sources"] == ["yahoo"]
