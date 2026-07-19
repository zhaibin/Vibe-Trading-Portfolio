from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import InstrumentRow, PositionRow


def write_headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key, "Origin": "http://127.0.0.1:8765"}


@pytest_asyncio.fixture
async def database(tmp_path: pytest.TempPathFactory) -> AsyncIterator[Database]:
    database = Database(tmp_path / "portfolio.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(build_portfolio_router(PortfolioService(database)))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        yield client


async def create_account(client: httpx.AsyncClient, *, name: str = "人民币账户") -> dict[str, object]:
    response = await client.post(
        "/api/v1/accounts",
        json={"name": name, "currency": "CNY", "cash_balance": "12.340000"},
        headers=write_headers(f"create-{name.encode().hex()}"),
    )
    assert response.status_code == 201
    return response.json()


async def test_create_account_returns_exact_decimal_strings_and_unknown_cash(client: httpx.AsyncClient) -> None:
    known = await create_account(client)
    unknown_response = await client.post(
        "/api/v1/accounts",
        json={"name": "港股账户", "currency": "HKD", "cash_balance": None},
        headers=write_headers("account-create-unknown"),
    )

    assert known["cash_balance"] == "12.340000"
    assert isinstance(known["cash_balance"], str)
    assert unknown_response.status_code == 201
    assert unknown_response.json()["cash_balance"] is None


async def test_create_rejects_normalized_duplicate_active_name(client: httpx.AsyncClient) -> None:
    await create_account(client, name="  港股\u3000账户 ")
    response = await client.post(
        "/api/v1/accounts",
        json={"name": "港股 账户", "currency": "HKD", "cash_balance": "0"},
        headers=write_headers("account-duplicate-name"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_ACCOUNT_NAME"


async def test_patch_rejects_stale_version(client: httpx.AsyncClient) -> None:
    account = await create_account(client)
    response = await client.patch(
        f"/api/v1/accounts/{account['id']}",
        json={"version": 0, "name": "已过期修改"},
        headers=write_headers("stale-edit"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONCURRENT_MODIFICATION"
    assert response.json()["error"]["fields"] == {"version": 1}


async def test_archive_is_blocked_while_account_has_active_positions(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add(
            InstrumentRow(
                id="22222222-2222-4222-8222-222222222222",
                canonical_symbol="600519.SH",
                name="测试证券",
                market="CN_SH",
                currency="CNY",
                asset_type="equity",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PositionRow(
                id="11111111-1111-4111-8111-111111111111",
                account_id=str(account["id"]),
                instrument_id="22222222-2222-4222-8222-222222222222",
                quantity=Decimal("1"),
                average_cost=Decimal("1"),
                note=None,
                version=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
            )
        )

    response = await client.patch(
        f"/api/v1/accounts/{account['id']}",
        json={"version": account["version"], "archived": True},
        headers=write_headers("archive-with-position"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_HAS_ACTIVE_POSITIONS"


async def test_list_accounts_is_cursor_paginated(client: httpx.AsyncClient) -> None:
    await create_account(client, name="A")
    await create_account(client, name="B")
    first = await client.get("/api/v1/accounts?limit=1")

    assert first.status_code == 200
    assert len(first.json()["items"]) == 1
    assert first.json()["next_cursor"]
    second = await client.get(f"/api/v1/accounts?limit=1&cursor={first.json()['next_cursor']}")
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    assert second.json()["items"][0]["id"] != first.json()["items"][0]["id"]


async def test_database_failure_is_sanitized(
    client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def fail_session() -> AsyncIterator[AsyncSession]:
        raise RuntimeError("sqlite path /private/secret must not leak")
        yield  # pragma: no cover

    monkeypatch.setattr(database, "session", fail_session)
    response = await client.get("/api/v1/accounts")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "PORTFOLIO_UNAVAILABLE"
    assert "private/secret" not in response.text
