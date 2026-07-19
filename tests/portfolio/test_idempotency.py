import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.repository import IdempotencyClaim, PortfolioRepository
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.schemas import AccountCreate
from vibe_portfolio.portfolio.service import PortfolioService, canonical_request_hash
from vibe_portfolio.portfolio.tables import AccountRow, AccountVersionRow, IdempotencyRow


def app_for(database: Database, repository: PortfolioRepository | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(build_portfolio_router(PortfolioService(database, repository)))
    return app


class CompletionFailureRepository(PortfolioRepository):
    async def complete_idempotency(
        self,
        session: AsyncSession,
        claim: IdempotencyClaim,
        account: AccountRow,
        status: int,
    ) -> None:
        await super().complete_idempotency(session, claim, account, status)
        raise RuntimeError("forced completion failure")


class HoldingCreateRepository(PortfolioRepository):
    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.entered = entered
        self.release = release

    async def create_account(
        self,
        session: AsyncSession,
        command: AccountCreate,
        normalized_name: str,
        now: datetime,
    ) -> AccountRow:
        self.entered.set()
        await self.release.wait()
        return await super().create_account(session, command, normalized_name, now)


class SignalingClaimRepository(PortfolioRepository):
    def __init__(self, started: asyncio.Event) -> None:
        self.started = started

    async def claim_idempotency(
        self,
        session: AsyncSession,
        scope: str,
        key: str,
        request_hash: str,
        now: datetime,
    ) -> IdempotencyClaim:
        self.started.set()
        return await super().claim_idempotency(session, scope, key, request_hash, now)


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


async def test_two_services_concurrent_identical_claims_wait_and_replay(database: Database) -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    first_app = app_for(database, HoldingCreateRepository(first_entered, release_first))
    second_app = app_for(database, SignalingClaimRepository(second_started))
    body = {"name": "并发账户", "currency": "CNY", "cash_balance": "0"}
    first_task = asyncio.create_task(request(first_app, body, "concurrent-identical-key"))
    await first_entered.wait()
    second_task = asyncio.create_task(request(second_app, body, "concurrent-identical-key"))
    await second_started.wait()
    try:
        await asyncio.sleep(0.05)
        assert not second_task.done()
    finally:
        release_first.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


async def test_two_services_concurrent_different_bodies_wait_then_conflict(database: Database) -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    first_app = app_for(database, HoldingCreateRepository(first_entered, release_first))
    second_app = app_for(database, SignalingClaimRepository(second_started))
    first_task = asyncio.create_task(
        request(first_app, {"name": "并发冲突", "currency": "CNY", "cash_balance": "1"}, "concurrent-body-key")
    )
    await first_entered.wait()
    second_task = asyncio.create_task(
        request(second_app, {"name": "并发冲突", "currency": "CNY", "cash_balance": "2"}, "concurrent-body-key")
    )
    await second_started.wait()
    try:
        await asyncio.sleep(0.05)
        assert not second_task.done()
    finally:
        release_first.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_committed_unexpired_pending_claim_is_never_treated_as_owner(database: Database) -> None:
    key = "abandoned-pending-key"
    body = {"name": "遗留待处理", "currency": "CNY", "cash_balance": "3"}
    command = AccountCreate.model_validate(body)
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add(
            IdempotencyRow(
                scope="POST:/api/v1/accounts",
                key_hash=sha256(key.encode()).hexdigest(),
                request_hash=canonical_request_hash(command.model_dump(mode="json", exclude_unset=True)),
                state="pending",
                resource_id=None,
                resource_version=None,
                response_status=None,
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )

    response = await request(app_for(database), body, key)
    async with database.session() as session:
        account_count = await session.scalar(select(func.count()).select_from(AccountRow))

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "PORTFOLIO_UNAVAILABLE"}}
    assert account_count == 0


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


async def test_post_completion_failure_rolls_back_account_history_and_claim(database: Database) -> None:
    key = "post-completion-failure"
    response = await request(
        app_for(database, CompletionFailureRepository()),
        {"name": "不得部分创建", "currency": "CNY", "cash_balance": "7"},
        key,
    )
    async with database.session() as session:
        account_count = await session.scalar(select(func.count()).select_from(AccountRow))
        history_count = await session.scalar(select(func.count()).select_from(AccountVersionRow))
        claim = await session.get(IdempotencyRow, ("POST:/api/v1/accounts", sha256(key.encode()).hexdigest()))

    assert response.status_code == 500
    assert account_count == 0
    assert history_count == 0
    assert claim is None


async def test_patch_completion_failure_rolls_back_version_name_cash_history_and_claim(database: Database) -> None:
    created = await request(
        app_for(database),
        {"name": "原始账户", "currency": "CNY", "cash_balance": "1.250000"},
        "atomic-patch-create",
    )
    account_id = str(created.json()["id"])
    key = "patch-completion-failure"
    response = await patch_request(
        app_for(database, CompletionFailureRepository()),
        account_id,
        {"version": 1, "name": "不得保留", "cash_balance": "9.750000"},
        key,
    )
    async with database.session() as session:
        account = await session.get(AccountRow, account_id)
        failed_history = await session.get(AccountVersionRow, (account_id, 2))
        claim = await session.get(
            IdempotencyRow,
            (f"PATCH:/api/v1/accounts/{account_id}", sha256(key.encode()).hexdigest()),
        )

    assert response.status_code == 500
    assert account is not None
    assert account.version == 1
    assert account.name == "原始账户"
    assert str(account.cash_balance) == "1.250000"
    assert failed_history is None
    assert claim is None


async def test_idempotency_storage_contains_only_hashes_and_non_sensitive_resource_metadata(
    database: Database,
) -> None:
    key = "storage-inspection-key"
    body = {"name": "不可原样存储", "currency": "CNY", "cash_balance": "7"}
    response = await request(app_for(database), body, key)
    async with database.session() as session:
        row = await session.get(IdempotencyRow, ("POST:/api/v1/accounts", sha256(key.encode()).hexdigest()))
        history = await session.get(AccountVersionRow, (str(response.json()["id"]), 1))

    assert response.status_code == 201
    assert row is not None
    assert row.key_hash != key
    assert row.request_hash != str(body)
    assert set(row.__table__.columns.keys()) == {
        "scope",
        "key_hash",
        "request_hash",
        "state",
        "resource_id",
        "resource_version",
        "response_status",
        "created_at",
        "expires_at",
    }
    assert row.resource_version == 1
    assert history is not None
    assert history.name == body["name"]
    assert history.cash_balance == body["cash_balance"]


async def test_replay_with_missing_version_history_fails_closed(database: Database) -> None:
    body = {"name": "缺失历史", "currency": "CNY", "cash_balance": "4"}
    key = "missing-history-key"
    created = await request(app_for(database), body, key)
    async with database.session() as session, session.begin():
        await session.execute(
            text("delete from account_versions where account_id = :account_id and version = 1"),
            {"account_id": created.json()["id"]},
        )

    replay = await request(app_for(database), body, key)

    assert replay.status_code == 503
    assert replay.json() == {"error": {"code": "PORTFOLIO_UNAVAILABLE"}}


@pytest.mark.parametrize(
    ("assignment", "value"),
    [("resource_version", None), ("response_status", 202)],
)
async def test_replay_with_invalid_metadata_fails_closed(
    database: Database, assignment: str, value: object
) -> None:
    body = {"name": "元数据损坏", "currency": "CNY", "cash_balance": "5"}
    key = f"invalid-metadata-{assignment}"
    created = await request(app_for(database), body, key)
    async with database.session() as session, session.begin():
        await session.execute(
            update(IdempotencyRow)
            .where(IdempotencyRow.key_hash == sha256(key.encode()).hexdigest())
            .values({assignment: value})
        )

    replay = await request(app_for(database), body, key)

    assert created.status_code == 201
    assert replay.status_code == 503
    assert replay.json() == {"error": {"code": "PORTFOLIO_UNAVAILABLE"}}


async def test_replay_with_malformed_history_is_validated_and_sanitized(database: Database) -> None:
    body = {"name": "历史损坏", "currency": "CNY", "cash_balance": "6"}
    key = "malformed-history-key"
    created = await request(app_for(database), body, key)
    async with database.session() as session, session.begin():
        await session.execute(
            text("update account_versions set cash_balance = :cash where account_id = :account_id"),
            {"cash": "private-malformed-value", "account_id": created.json()["id"]},
        )

    replay = await request(app_for(database), body, key)

    assert replay.status_code == 503
    assert replay.json() == {"error": {"code": "PORTFOLIO_UNAVAILABLE"}}
    assert "private-malformed-value" not in replay.text
