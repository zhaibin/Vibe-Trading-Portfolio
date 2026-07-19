from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService


def app_for(database: Database) -> FastAPI:
    app = FastAPI()
    app.include_router(build_portfolio_router(PortfolioService(database)))
    return app


@pytest_asyncio.fixture
async def database(tmp_path: pytest.TempPathFactory) -> AsyncIterator[Database]:
    database = Database(tmp_path / "portfolio.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


async def request(app: FastAPI, body: dict[str, object], key: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        return await client.post(
            "/api/v1/accounts",
            json=body,
            headers={"Idempotency-Key": key, "Origin": "http://127.0.0.1:8765"},
        )


async def test_create_account_replays_same_idempotency_key_after_service_restart(database: Database) -> None:
    body = {"name": "港股账户", "currency": "HKD", "cash_balance": None}
    first = await request(app_for(database), body, "account-create-1")
    await database.close()
    await database.start()
    second = await request(app_for(database), body, "account-create-1")

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


async def test_same_idempotency_key_with_different_body_is_a_conflict(database: Database) -> None:
    first = await request(
        app_for(database),
        {"name": "美元账户", "currency": "USD", "cash_balance": "1"},
        "account-create-conflict",
    )
    second = await request(
        app_for(database),
        {"name": "美元账户", "currency": "USD", "cash_balance": "2"},
        "account-create-conflict",
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_idempotency_key_must_be_visible_ascii_and_is_not_accepted_when_too_short(database: Database) -> None:
    response = await request(
        app_for(database),
        {"name": "美元账户", "currency": "USD", "cash_balance": "0"},
        "short",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
