import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import IdempotencyRow


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


async def patch_request(app: FastAPI, account_id: str, body: dict[str, object], key: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        return await client.patch(
            f"/api/v1/accounts/{account_id}",
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


async def test_post_replay_is_original_snapshot_after_later_mutation_and_restart(database: Database) -> None:
    body = {"name": "回放账户", "currency": "CNY", "cash_balance": "1"}
    first = await request(app_for(database), body, "snapshot-create-key")
    changed = await patch_request(
        app_for(database), str(first.json()["id"]), {"version": 1, "name": "后来修改"}, "later-edit-key"
    )
    await database.close()
    await database.start()
    replay = await request(app_for(database), body, "snapshot-create-key")

    assert first.status_code == replay.status_code == 201
    assert changed.status_code == 200
    assert replay.json() == first.json()


async def test_patch_replay_is_original_snapshot_after_later_mutation_and_restart(database: Database) -> None:
    created = await request(
        app_for(database), {"name": "补丁回放", "currency": "CNY", "cash_balance": "1"}, "patch-snapshot-create"
    )
    account_id = str(created.json()["id"])
    first = await patch_request(app_for(database), account_id, {"version": 1, "name": "第一次"}, "snapshot-patch-key")
    changed = await patch_request(app_for(database), account_id, {"version": 2, "name": "第二次"}, "later-patch-key")
    await database.close()
    await database.start()
    replay = await patch_request(app_for(database), account_id, {"version": 1, "name": "第一次"}, "snapshot-patch-key")

    assert first.status_code == replay.status_code == 200
    assert changed.status_code == 200
    assert replay.json() == first.json()


async def test_concurrent_identical_idempotency_claims_converge_to_successful_replay(database: Database) -> None:
    app = app_for(database)
    body = {"name": "并发账户", "currency": "CNY", "cash_balance": "0"}
    first, second = await asyncio.gather(
        request(app, body, "concurrent-identical-key"), request(app, body, "concurrent-identical-key")
    )

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


async def test_expired_key_can_be_reclaimed_with_a_different_body(database: Database) -> None:
    key = "expired-key-reclaim"
    first = await request(app_for(database), {"name": "过期原始", "currency": "CNY", "cash_balance": "1"}, key)
    assert first.status_code == 201
    async with database.session() as session, session.begin():
        row = await session.get(IdempotencyRow, ("POST:/api/v1/accounts", sha256(key.encode()).hexdigest()))
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(days=2)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    reclaimed = await request(app_for(database), {"name": "过期新值", "currency": "CNY", "cash_balance": "2"}, key)

    assert reclaimed.status_code == 201
    assert reclaimed.json()["name"] == "过期新值"


async def test_idempotency_storage_keeps_only_hashes_and_response_snapshot(database: Database) -> None:
    key = "storage-inspection-key"
    body = {"name": "不可原样存储", "currency": "CNY", "cash_balance": "7"}
    response = await request(app_for(database), body, key)
    async with database.session() as session:
        row = await session.get(IdempotencyRow, ("POST:/api/v1/accounts", sha256(key.encode()).hexdigest()))

    assert response.status_code == 201
    assert row is not None
    assert row.key_hash != key
    assert row.request_hash != str(body)
    assert row.response_json is not None
    assert key not in row.response_json
