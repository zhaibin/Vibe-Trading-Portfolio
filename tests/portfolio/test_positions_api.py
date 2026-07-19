import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from vibe_portfolio.portfolio.database import Database, DatabaseBusyError, DatabaseStartupError
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import InstrumentCandidateRow


def write_headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key, "Origin": "http://127.0.0.1:8765"}


def app_for_service(service: PortfolioService) -> FastAPI:
    app = FastAPI()
    app.include_router(build_portfolio_router(service))
    return app


def app_for(database: Database) -> FastAPI:
    return app_for_service(PortfolioService(database))


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
    await client.patch(
        f"/api/v1/positions/{first.json()['id']}",
        json={"version": 1, "archived": True},
        headers=write_headers("page-archive-first"),
    )

    active = await client.get("/api/v1/positions?limit=1")
    archived = await client.get("/api/v1/positions?archived=true&limit=1")

    assert second.status_code == 201
    assert active.status_code == archived.status_code == 200
    assert [item["id"] for item in active.json()["items"]] == [second.json()["id"]]
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
