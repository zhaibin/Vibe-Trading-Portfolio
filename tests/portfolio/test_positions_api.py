import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from vibe_portfolio.portfolio.database import Database, DatabaseBusyError, DatabaseStartupError
from vibe_portfolio.portfolio.repository import IdempotencyClaim, PortfolioRepository, hash_idempotency_key
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.schemas import PositionPatch
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import IdempotencyRow, InstrumentCandidateRow, PositionRow, PositionVersionRow


def write_headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key, "Origin": "http://127.0.0.1:8765"}


def app_for_service(service: PortfolioService) -> FastAPI:
    app = FastAPI()
    app.include_router(build_portfolio_router(service))
    return app


def app_for(database: Database, repository: PortfolioRepository | None = None) -> FastAPI:
    return app_for_service(PortfolioService(database, repository))


async def patch_for_app(
    app: FastAPI, position_id: str, body: dict[str, object], key: str
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as app_client:
        return await app_client.patch(
            f"/api/v1/positions/{position_id}",
            json=body,
            headers=write_headers(key),
        )


class HoldingPositionRepository(PortfolioRepository):
    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.entered = entered
        self.release = release

    async def update_position(
        self,
        session: AsyncSession,
        position_id: str,
        command: PositionPatch,
        note: str | None,
        now: datetime,
    ) -> PositionRow:
        self.entered.set()
        await self.release.wait()
        return await super().update_position(session, position_id, command, note, now)


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


class CompletionFailureRepository(PortfolioRepository):
    async def complete_resource_idempotency(
        self,
        session: AsyncSession,
        claim: IdempotencyClaim,
        *,
        resource_id: str,
        resource_version: int,
        status: int,
    ) -> None:
        await super().complete_resource_idempotency(
            session,
            claim,
            resource_id=resource_id,
            resource_version=resource_version,
            status=status,
        )
        raise RuntimeError("forced completion failure")


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
    transport = httpx.ASGITransport(app=app_for(database), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        yield client


async def create_account(
    client: httpx.AsyncClient, *, name: str = "人民币账户", currency: str = "CNY"
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/accounts",
        json={"name": name, "currency": currency, "cash_balance": "0"},
        headers=write_headers(f"create-account-{name.encode().hex()}"),
    )
    assert response.status_code == 201
    return response.json()


async def confirmed_instrument(
    client: httpx.AsyncClient,
    database: Database,
    *,
    symbol: str = "600519.SH",
    market: str = "CN_SH",
    currency: str = "CNY",
    asset_type: str = "equity",
) -> dict[str, object]:
    now = datetime.now(UTC)
    candidate_id = str(uuid4())
    async with database.session() as session, session.begin():
        session.add(
            InstrumentCandidateRow(
                id=candidate_id,
                canonical_symbol=symbol,
                name=f"示例-{symbol}",
                market=market,
                currency=currency,
                asset_type=asset_type,
                provider="fixture",
                provider_symbols_json=json.dumps(
                    [{"provider": "fixture", "symbol": f"fixture-{candidate_id}"}]
                ),
                created_at=now,
                expires_at=now + timedelta(minutes=15),
                consumed_at=None,
            )
        )
    response = await client.post(
        "/api/v1/instruments/confirm",
        json={"candidate_id": candidate_id},
        headers=write_headers(f"confirm-{candidate_id}"),
    )
    assert response.status_code == 201
    return response.json()


async def create_position(
    client: httpx.AsyncClient,
    account: dict[str, object],
    instrument: dict[str, object],
    *,
    key: str = "create-position-1",
    quantity: object = "10.00000000",
    average_cost: object = "12.340000",
    note: object = "长期持有",
) -> httpx.Response:
    return await client.post(
        "/api/v1/positions",
        json={
            "account_id": account["id"],
            "instrument_id": instrument["id"],
            "quantity": quantity,
            "average_cost": average_cost,
            "note": note,
        },
        headers=write_headers(key),
    )


async def test_position_views_embed_local_instrument_identity_and_replay_it(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database, symbol="600519.SH")
    key = "position-with-instrument-summary"
    created = await create_position(client, account, instrument, key=key)
    replay = await create_position(client, account, instrument, key=key)
    listed = await client.get("/api/v1/positions")

    expected = {
        "canonical_symbol": "600519.SH",
        "name": "示例-600519.SH",
        "market": "CN_SH",
        "currency": "CNY",
        "asset_type": "equity",
    }
    assert created.status_code == replay.status_code == 201
    assert listed.status_code == 200
    assert created.json()["instrument"] == expected
    assert replay.json() == created.json()
    assert listed.json()["items"][0]["instrument"] == expected


async def test_confirmed_candidate_is_required(client: httpx.AsyncClient) -> None:
    account = await create_account(client)
    response = await client.post(
        "/api/v1/positions",
        json={
            "account_id": account["id"],
            "instrument_id": str(uuid4()),
            "quantity": "10",
            "average_cost": "12.34",
        },
        headers=write_headers("position-unconfirmed"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSTRUMENT_NOT_CONFIRMED"


async def test_position_currency_must_match_account(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(
        client, database, symbol="AAPL.US", market="US", currency="USD"
    )

    response = await create_position(client, account, instrument, key="position-currency-mismatch")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CURRENCY_MISMATCH"


async def test_position_exact_decimals_round_trip_and_persist_after_restart(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument)
    await database.close()
    await database.start()

    listed = await client.get("/api/v1/positions")

    assert created.status_code == 201
    assert created.json()["quantity"] == "10.00000000"
    assert created.json()["average_cost"] == "12.340000"
    assert listed.status_code == 200
    assert listed.json()["items"] == [created.json()]


@pytest.mark.parametrize(
    ("quantity", "average_cost"),
    [
        (10, "12.34"),
        ("10", 12.34),
        ("-1", "12.34"),
        ("1.000000001", "12.34"),
        ("10", "1.0000001"),
    ],
)
async def test_position_rejects_non_string_short_or_overprecision_values(
    client: httpx.AsyncClient,
    database: Database,
    quantity: object,
    average_cost: object,
) -> None:
    account = await create_account(client, name=f"账户-{quantity}-{average_cost}")
    instrument = await confirmed_instrument(client, database)

    response = await create_position(
        client,
        account,
        instrument,
        key=f"invalid-position-{quantity}-{average_cost}",
        quantity=quantity,
        average_cost=average_cost,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_duplicate_active_position_is_a_stable_conflict(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    first = await create_position(client, account, instrument, key="duplicate-position-first")
    duplicate = await create_position(client, account, instrument, key="duplicate-position-second")

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_POSITION"


async def test_position_key_with_different_body_is_an_idempotency_conflict(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)

    first = await create_position(
        client,
        account,
        instrument,
        key="position-body-conflict",
        quantity="1",
    )
    conflict = await create_position(
        client,
        account,
        instrument,
        key="position-body-conflict",
        quantity="2",
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_archive_allows_recreate_but_restore_rejects_active_duplicate(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    first = await create_position(client, account, instrument, key="archive-first-position")
    archived = await client.patch(
        f"/api/v1/positions/{first.json()['id']}",
        json={"version": 1, "archived": True},
        headers=write_headers("archive-position"),
    )
    recreated = await create_position(client, account, instrument, key="recreate-position")
    restored = await client.patch(
        f"/api/v1/positions/{first.json()['id']}",
        json={"version": 2, "archived": False},
        headers=write_headers("restore-position-conflict"),
    )

    assert archived.status_code == 200
    assert recreated.status_code == 201
    assert restored.status_code == 409
    assert restored.json()["error"]["code"] == "DUPLICATE_POSITION"


async def test_explicit_restore_then_position_edit_succeeds(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key="restore-success-create")
    archived = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 1, "archived": True},
        headers=write_headers("restore-success-archive"),
    )

    restored = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 2, "archived": False},
        headers=write_headers("restore-success"),
    )
    edited = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 3, "quantity": "25"},
        headers=write_headers("edit-after-restore"),
    )

    assert archived.status_code == 200
    assert restored.status_code == 200
    assert restored.json()["version"] == 3
    assert restored.json()["archived_at"] is None
    assert edited.status_code == 200
    assert edited.json()["version"] == 4
    assert edited.json()["quantity"] == "25"


async def test_archived_position_rejects_edits_without_mutating_state_or_idempotency(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key="archived-edit-create")
    position_id = str(created.json()["id"])
    archived = await client.patch(
        f"/api/v1/positions/{position_id}",
        json={"version": 1, "archived": True},
        headers=write_headers("archived-edit-archive"),
    )
    body = {
        "version": 2,
        "quantity": "25",
        "average_cost": "20.5",
        "note": "must not change",
    }
    first = await client.patch(
        f"/api/v1/positions/{position_id}",
        json=body,
        headers=write_headers("archived-edit-rejected"),
    )
    repeated = await client.patch(
        f"/api/v1/positions/{position_id}",
        json=body,
        headers=write_headers("archived-edit-rejected"),
    )
    async with database.session() as session:
        current = await session.get(PositionRow, position_id)
        history_count = await session.scalar(
            select(func.count())
            .select_from(PositionVersionRow)
            .where(PositionVersionRow.position_id == position_id)
        )
        claim = await session.get(
            IdempotencyRow,
            (f"PATCH:/api/v1/positions/{position_id}", hash_idempotency_key("archived-edit-rejected")),
        )

    assert archived.status_code == 200
    for response in (first, repeated):
        assert response.status_code == 409
        assert response.json() == {"error": {"code": "POSITION_ARCHIVED"}}
    assert current is not None
    assert current.version == 2
    assert format(current.quantity, "f") == created.json()["quantity"]
    assert format(current.average_cost, "f") == created.json()["average_cost"]
    assert current.note == created.json()["note"]
    assert current.archived_at is not None
    assert history_count == 2
    assert claim is None


async def test_archive_idempotency_replays_exact_archived_snapshot_after_restore(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key="archive-replay-create")
    position_id = str(created.json()["id"])
    archive_body = {"version": 1, "archived": True}
    archive_key = "archive-replay-exact"
    archived = await client.patch(
        f"/api/v1/positions/{position_id}",
        json=archive_body,
        headers=write_headers(archive_key),
    )
    restored = await client.patch(
        f"/api/v1/positions/{position_id}",
        json={"version": 2, "archived": False},
        headers=write_headers("archive-replay-restore"),
    )
    replay = await client.patch(
        f"/api/v1/positions/{position_id}",
        json=archive_body,
        headers=write_headers(archive_key),
    )
    async with database.session() as session:
        current = await session.get(PositionRow, position_id)

    assert archived.status_code == restored.status_code == replay.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert replay.json() == archived.json()
    assert current is not None
    assert current.version == 3
    assert current.archived_at is None


async def test_combined_restore_and_field_edits_are_atomic_and_replay_exactly(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key="combined-restore-create")
    position_id = str(created.json()["id"])
    archived = await client.patch(
        f"/api/v1/positions/{position_id}",
        json={"version": 1, "archived": True},
        headers=write_headers("combined-restore-archive"),
    )
    restore_body = {
        "version": 2,
        "archived": False,
        "quantity": "25",
        "average_cost": "20.5",
        "note": "恢复后 👩‍💻",
    }
    restore_key = "combined-restore-fields"
    restored = await client.patch(
        f"/api/v1/positions/{position_id}",
        json=restore_body,
        headers=write_headers(restore_key),
    )
    later = await client.patch(
        f"/api/v1/positions/{position_id}",
        json={"version": 3, "quantity": "30"},
        headers=write_headers("combined-restore-later-edit"),
    )
    replay = await client.patch(
        f"/api/v1/positions/{position_id}",
        json=restore_body,
        headers=write_headers(restore_key),
    )

    assert archived.status_code == restored.status_code == later.status_code == replay.status_code == 200
    assert restored.json()["version"] == 3
    assert restored.json()["archived_at"] is None
    assert restored.json()["quantity"] == "25"
    assert restored.json()["average_cost"] == "20.5"
    assert restored.json()["note"] == "恢复后 👩‍💻"
    assert later.json()["version"] == 4
    assert replay.json() == restored.json()


async def test_patch_uses_optimistic_concurrency(client: httpx.AsyncClient, database: Database) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key="concurrency-position")

    response = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 0, "quantity": "2"},
        headers=write_headers("stale-position-patch"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONCURRENT_MODIFICATION"
    assert response.json()["error"]["fields"] == {"version": 1}


async def test_concurrent_position_patches_have_one_winner(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key="concurrent-patch-create")
    position_id = str(created.json()["id"])
    entered = asyncio.Event()
    release = asyncio.Event()
    second_started = asyncio.Event()
    first_app = app_for(database, HoldingPositionRepository(entered, release))
    second_app = app_for(database, SignalingClaimRepository(second_started))
    first_task = asyncio.create_task(
        patch_for_app(first_app, position_id, {"version": 1, "quantity": "2"}, "concurrent-patch-first")
    )
    await entered.wait()
    second_task = asyncio.create_task(
        patch_for_app(second_app, position_id, {"version": 1, "quantity": "3"}, "concurrent-patch-second")
    )
    await second_started.wait()
    try:
        await asyncio.sleep(0.05)
        assert not second_task.done()
    finally:
        release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONCURRENT_MODIFICATION"
    assert second.json()["error"]["fields"] == {"version": 2}


async def test_notes_are_normalized_and_control_characters_or_long_values_are_rejected(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    normalized = await create_position(
        client,
        account,
        instrument,
        key="normalized-position-note",
        note="Ａ股观察",
    )
    control = await create_position(
        client,
        account,
        instrument,
        key="control-position-note",
        note="数据\u0000指令",
    )
    too_long = await create_position(
        client,
        account,
        instrument,
        key="long-position-note",
        note="字" * 501,
    )

    assert normalized.status_code == 201
    assert normalized.json()["note"] == "A股观察"
    for response in (control, too_long):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_note_allows_plain_unicode_formatting_sequences(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)

    response = await create_position(
        client,
        account,
        instrument,
        key="unicode-position-note",
        note="家庭 👩‍💻",
    )

    assert response.status_code == 201
    assert response.json()["note"] == "家庭 👩‍💻"


@pytest.mark.parametrize("unsafe", ["\u202e", "\u2066", "\u2067", "\u2068", "\u2069"])
async def test_note_rejects_bidi_format_controls(
    client: httpx.AsyncClient, database: Database, unsafe: str
) -> None:
    account = await create_account(client, name=f"bidi-{ord(unsafe):x}")
    instrument = await confirmed_instrument(client, database)

    response = await create_position(
        client,
        account,
        instrument,
        key=f"bidi-position-note-{ord(unsafe):x}",
        note=f"safe{unsafe}spoofed",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_archived_account_rejects_new_position(client: httpx.AsyncClient, database: Database) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    archived = await client.patch(
        f"/api/v1/accounts/{account['id']}",
        json={"version": 1, "archived": True},
        headers=write_headers("archive-empty-account"),
    )

    response = await create_position(client, account, instrument, key="position-archived-account")

    assert archived.status_code == 200
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_ARCHIVED"


async def test_account_currency_field_remains_immutable_with_active_position(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key="position-before-currency-attempt")

    response = await client.patch(
        f"/api/v1/accounts/{account['id']}",
        json={"version": 1, "currency": "USD"},
        headers=write_headers("account-currency-attempt"),
    )
    listed = await client.get("/api/v1/accounts")

    assert created.status_code == 201
    assert response.status_code == 422
    assert listed.json()["items"][0]["currency"] == "CNY"
    assert listed.json()["items"][0]["version"] == 1


async def test_active_and_archived_position_lists_are_cursor_paginated(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    first_instrument = await confirmed_instrument(client, database)
    second_instrument = await confirmed_instrument(
        client, database, symbol="000001.SZ", market="CN_SZ", currency="CNY", asset_type="equity"
    )
    first = await create_position(client, account, first_instrument, key="page-position-first")
    second = await create_position(client, account, second_instrument, key="page-position-second")

    first_page = await client.get("/api/v1/positions?limit=1")
    second_page = await client.get(
        f"/api/v1/positions?limit=1&cursor={first_page.json()['next_cursor']}"
    )
    await client.patch(
        f"/api/v1/positions/{first.json()['id']}",
        json={"version": 1, "archived": True},
        headers=write_headers("page-archive-first"),
    )

    archived = await client.get("/api/v1/positions?archived=true&limit=1")

    assert second.status_code == 201
    assert first_page.status_code == second_page.status_code == archived.status_code == 200
    assert first_page.json()["next_cursor"]
    assert second_page.json()["next_cursor"] is None
    assert {
        first_page.json()["items"][0]["id"],
        second_page.json()["items"][0]["id"],
    } == {first.json()["id"], second.json()["id"]}
    assert [item["id"] for item in archived.json()["items"]] == [first.json()["id"]]


async def test_patch_replay_returns_original_position_after_later_update_and_restart(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key="position-history-create")
    first = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 1, "quantity": "2"},
        headers=write_headers("position-history-patch"),
    )
    later = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 2, "quantity": "3"},
        headers=write_headers("position-later-patch"),
    )
    await database.close()
    await database.start()

    replay = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 1, "quantity": "2"},
        headers=write_headers("position-history-patch"),
    )

    assert first.status_code == replay.status_code == 200
    assert later.status_code == 200
    assert replay.json() == first.json()


@pytest.mark.parametrize(
    ("assignment", "value"),
    [
        ("note", "bad\u0000note"),
        ("note", "Ａ股观察"),
        ("created_at", "2030-07-19T00:00:00+00:00"),
        ("updated_at", "2020-07-19T00:00:00+00:00"),
    ],
    ids=["nul-note", "nonnormal-note", "created-after-updated", "updated-before-created"],
)
async def test_position_replay_rejects_tampered_history(
    client: httpx.AsyncClient,
    database: Database,
    assignment: str,
    value: str,
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    key = f"tampered-position-history-{assignment}-{uuid4()}"
    created = await create_position(client, account, instrument, key=key)
    async with database.session() as session, session.begin():
        await session.execute(
            update(PositionVersionRow)
            .where(PositionVersionRow.position_id == created.json()["id"], PositionVersionRow.version == 1)
            .values({assignment: value})
        )

    replay = await create_position(client, account, instrument, key=key)

    assert created.status_code == 201
    assert replay.status_code == 503
    assert replay.json() == {"error": {"code": "PORTFOLIO_UNAVAILABLE"}}
    assert value not in replay.text


@pytest.mark.parametrize(
    ("assignment", "value"),
    [
        ("archived_at", "2020-07-19T00:00:00+00:00"),
        ("updated_at", "2030-07-19T00:00:00+00:00"),
    ],
    ids=["archive-before-created", "updated-after-archive"],
)
async def test_archived_position_replay_rejects_reversed_timestamps(
    client: httpx.AsyncClient,
    database: Database,
    assignment: str,
    value: str,
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    created = await create_position(client, account, instrument, key=f"archive-tamper-create-{uuid4()}")
    key = f"archive-tamper-patch-{uuid4()}"
    archived = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 1, "archived": True},
        headers=write_headers(key),
    )
    async with database.session() as session, session.begin():
        await session.execute(
            update(PositionVersionRow)
            .where(PositionVersionRow.position_id == created.json()["id"], PositionVersionRow.version == 2)
            .values({assignment: value})
        )

    replay = await client.patch(
        f"/api/v1/positions/{created.json()['id']}",
        json={"version": 1, "archived": True},
        headers=write_headers(key),
    )

    assert archived.status_code == 200
    assert replay.status_code == 503
    assert replay.json() == {"error": {"code": "PORTFOLIO_UNAVAILABLE"}}


async def test_position_replay_rejects_tampered_resource_version(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    key = "tampered-position-version"
    created = await create_position(client, account, instrument, key=key)
    async with database.session() as session, session.begin():
        await session.execute(
            update(IdempotencyRow)
            .where(IdempotencyRow.key_hash == hash_idempotency_key(key))
            .values(resource_version=0)
        )

    replay = await create_position(client, account, instrument, key=key)

    assert created.status_code == 201
    assert replay.status_code == 503
    assert replay.json() == {"error": {"code": "PORTFOLIO_UNAVAILABLE"}}


async def test_position_completion_failure_rolls_back_position_history_and_claim(
    client: httpx.AsyncClient, database: Database
) -> None:
    account = await create_account(client)
    instrument = await confirmed_instrument(client, database)
    key = "position-completion-failure"
    transport = httpx.ASGITransport(
        app=app_for(database, CompletionFailureRepository()),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as failing_client:
        response = await create_position(failing_client, account, instrument, key=key)
    async with database.session() as session:
        position_count = await session.scalar(select(func.count()).select_from(PositionRow))
        history_count = await session.scalar(select(func.count()).select_from(PositionVersionRow))
        claim = await session.get(
            IdempotencyRow,
            ("POST:/api/v1/positions", hash_idempotency_key(key)),
        )

    assert response.status_code == 500
    assert position_count == 0
    assert history_count == 0
    assert claim is None


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (DatabaseBusyError(), 503, "DATABASE_BUSY"),
        (DatabaseStartupError("DATABASE_STARTUP_FAILED"), 500, "PORTFOLIO_UNAVAILABLE"),
        (RuntimeError("private database path"), 500, "PORTFOLIO_UNAVAILABLE"),
    ],
)
async def test_position_storage_failures_use_sanitized_errors(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    status: int,
    code: str,
) -> None:
    service = PortfolioService(database)

    async def fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise failure

    monkeypatch.setattr(service, "list_positions", fail)
    monkeypatch.setattr(service, "create_position", fail)
    monkeypatch.setattr(service, "update_position", fail)
    transport = httpx.ASGITransport(app=app_for_service(service), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as failing_client:
        responses = [
            await failing_client.get("/api/v1/positions"),
            await failing_client.post(
                "/api/v1/positions",
                json={
                    "account_id": str(uuid4()),
                    "instrument_id": str(uuid4()),
                    "quantity": "1",
                    "average_cost": "1",
                },
                headers=write_headers(f"create-failure-{uuid4()}"),
            ),
            await failing_client.patch(
                f"/api/v1/positions/{uuid4()}",
                json={"version": 1, "quantity": "1"},
                headers=write_headers(f"patch-failure-{uuid4()}"),
            ),
        ]

    for response in responses:
        assert response.status_code == status
        assert response.json() == {"error": {"code": code}}
        assert "private database path" not in response.text
