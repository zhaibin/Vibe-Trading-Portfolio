import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from vibe_portfolio.portfolio.database import Database, DatabaseBusyError, DatabaseStartupError
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.repository import (
    IdempotencyClaim,
    InstrumentNotConfirmed,
    PortfolioRepository,
    hash_idempotency_key,
)
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import (
    IdempotencyRow,
    InstrumentCandidateRow,
    InstrumentProviderSymbolRow,
    InstrumentRow,
)


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


async def add_candidate(
    database: Database,
    *,
    canonical_symbol: str = "600519.SH",
    name: str = "示例股票",
    market: str = "CN_SH",
    currency: str = "CNY",
    asset_type: str = "equity",
    expired: bool = False,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    now = datetime.now(UTC)
    stored_created_at = created_at or (now - timedelta(hours=1) if expired else now)
    stored_expires_at = expires_at or (now - timedelta(seconds=1) if expired else now + timedelta(minutes=15))
    candidate_id = str(uuid4())
    provider_symbol = f"provider-{candidate_id}"
    async with database.session() as session, session.begin():
        session.add(
            InstrumentCandidateRow(
                id=candidate_id,
                canonical_symbol=canonical_symbol,
                name=name,
                market=market,
                currency=currency,
                asset_type=asset_type,
                provider="fixture",
                provider_symbols_json=json.dumps([{"provider": "fixture", "symbol": provider_symbol}]),
                created_at=stored_created_at,
                expires_at=stored_expires_at,
                consumed_at=None,
            )
        )
    return candidate_id


async def confirm(client: httpx.AsyncClient, candidate_id: str, key: str) -> httpx.Response:
    return await client.post(
        "/api/v1/instruments/confirm",
        json={"candidate_id": candidate_id},
        headers=write_headers(key),
    )


def app_for(database: Database, repository: PortfolioRepository | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(build_portfolio_router(PortfolioService(database, repository)))
    return app


async def confirm_for_app(app: FastAPI, candidate_id: str, key: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as app_client:
        return await confirm(app_client, candidate_id, key)


class HoldingInstrumentRepository(PortfolioRepository):
    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.entered = entered
        self.release = release

    async def upsert_instrument(
        self,
        session: AsyncSession,
        candidate: InstrumentCandidateRow,
        provider_symbols: Sequence[tuple[str, str]],
        now: datetime,
    ) -> InstrumentRow:
        self.entered.set()
        await self.release.wait()
        return await super().upsert_instrument(session, candidate, provider_symbols, now)


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


@dataclass(frozen=True)
class ProviderSymbolFixture:
    provider: str
    symbol: str


@dataclass(frozen=True)
class CandidateFixture:
    canonical_symbol: str
    name: str
    market: Market
    currency: Currency
    asset_type: AssetType
    provider_symbols: tuple[ProviderSymbolFixture, ...]


async def test_repository_caches_only_normalized_candidate_fields(database: Database) -> None:
    now = datetime.now(UTC)
    candidate = CandidateFixture(
        canonical_symbol="00700.HK",
        name="示例证券",
        market=Market.HK,
        currency=Currency.HKD,
        asset_type=AssetType.EQUITY,
        provider_symbols=(ProviderSymbolFixture("fixture", "0700.HK"),),
    )
    repository = PortfolioRepository()
    parameters = inspect.signature(repository.cache_candidates).parameters
    assert set(parameters) == {"session", "candidates", "now"}
    async with database.session() as session, session.begin():
        rows = await repository.cache_candidates(
            session,
            [candidate],
            now=now,
        )

    assert len(rows) == 1
    assert rows[0].canonical_symbol == "00700.HK"
    assert rows[0].market == "HK"
    assert rows[0].created_at == now
    assert rows[0].expires_at == now + timedelta(minutes=15)
    assert json.loads(rows[0].provider_symbols_json) == [{"provider": "fixture", "symbol": "0700.HK"}]


async def test_repository_rejects_candidate_without_provider_mapping(database: Database) -> None:
    now = datetime.now(UTC)
    candidate = CandidateFixture(
        canonical_symbol="00700.HK",
        name="示例证券",
        market=Market.HK,
        currency=Currency.HKD,
        asset_type=AssetType.EQUITY,
        provider_symbols=(),
    )
    async with database.session() as session, session.begin():
        with pytest.raises(InstrumentNotConfirmed):
            await PortfolioRepository().cache_candidates(
                session,
                [candidate],
                now=now,
            )


@pytest.mark.parametrize(
    ("symbol", "market", "currency", "asset_type"),
    [
        ("600519.SH", "CN_SH", "CNY", "equity"),
        ("510300.SH", "CN_SH", "CNY", "etf"),
    ],
)
async def test_confirm_accepts_server_cached_equity_and_etf(
    client: httpx.AsyncClient,
    database: Database,
    symbol: str,
    market: str,
    currency: str,
    asset_type: str,
) -> None:
    candidate_id = await add_candidate(
        database,
        canonical_symbol=symbol,
        market=market,
        currency=currency,
        asset_type=asset_type,
    )

    response = await confirm(client, candidate_id, f"confirm-{asset_type}")

    assert response.status_code == 201
    assert response.json()["canonical_symbol"] == symbol
    assert response.json()["market"] == market
    assert response.json()["currency"] == currency
    assert response.json()["asset_type"] == asset_type


async def test_confirm_rejects_expired_and_unknown_candidate_ids(
    client: httpx.AsyncClient, database: Database
) -> None:
    expired_id = await add_candidate(database, expired=True)

    expired = await confirm(client, expired_id, "confirm-expired")
    unknown = await confirm(client, str(uuid4()), "confirm-unknown")

    for response in (expired, unknown):
        assert response.status_code == 422
        assert response.json() == {"error": {"code": "INSTRUMENT_NOT_CONFIRMED"}}


async def test_confirmation_accepts_exact_fifteen_minute_candidate_lifetime(
    client: httpx.AsyncClient, database: Database
) -> None:
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    candidate_id = await add_candidate(
        database,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=15),
    )

    response = await confirm(client, candidate_id, "confirm-exact-lifetime")

    assert response.status_code == 201


@pytest.mark.parametrize(
    ("created_offset", "lifetime"),
    [
        (timedelta(0), timedelta(minutes=15, microseconds=1)),
        (timedelta(seconds=1), timedelta(minutes=15)),
        (timedelta(minutes=-16), timedelta(minutes=15)),
    ],
    ids=["just-over-max", "future-created", "expired"],
)
async def test_confirmation_rejects_untrusted_candidate_lifetime(
    client: httpx.AsyncClient,
    database: Database,
    created_offset: timedelta,
    lifetime: timedelta,
) -> None:
    created_at = datetime.now(UTC) + created_offset
    candidate_id = await add_candidate(
        database,
        created_at=created_at,
        expires_at=created_at + lifetime,
    )

    response = await confirm(client, candidate_id, f"confirm-invalid-lifetime-{uuid4()}")

    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INSTRUMENT_NOT_CONFIRMED"}}


async def test_confirm_revalidates_tampered_server_candidate_without_consuming_it(
    client: httpx.AsyncClient, database: Database
) -> None:
    candidate_id = await add_candidate(database, canonical_symbol="AAPL.US", market="US", currency="CNY")

    response = await confirm(client, candidate_id, "confirm-tampered")
    async with database.session() as session:
        candidate = await session.scalar(
            select(InstrumentCandidateRow).where(InstrumentCandidateRow.id == candidate_id)
        )

    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INSTRUMENT_NOT_CONFIRMED"}}
    assert candidate is not None and candidate.consumed_at is None


@pytest.mark.parametrize(
    ("assignment", "value"),
    [
        ("canonical_symbol", "AAPL.HK"),
        ("name", "bad\u0000name"),
        ("provider_symbols_json", "{}"),
        ("provider_symbols_json", '[{"provider":"fixture","symbol":"x","extra":"bad"}]'),
        ("provider_symbols_json", '[{"provider":"fixture","symbol":"bad\\u0000symbol"}]'),
        ("provider", "missing-provider"),
    ],
)
async def test_confirmation_rejects_malformed_cached_identity(
    client: httpx.AsyncClient,
    database: Database,
    assignment: str,
    value: str,
) -> None:
    candidate_id = await add_candidate(database)
    async with database.session() as session, session.begin():
        await session.execute(
            update(InstrumentCandidateRow)
            .where(InstrumentCandidateRow.id == candidate_id)
            .values({assignment: value})
        )

    response = await confirm(client, candidate_id, f"malformed-{assignment}-{uuid4()}")

    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INSTRUMENT_NOT_CONFIRMED"}}


async def test_confirmation_accepts_only_candidate_id_from_browser(
    client: httpx.AsyncClient, database: Database
) -> None:
    candidate_id = await add_candidate(database)

    rejected = await client.post(
        "/api/v1/instruments/confirm",
        json={"candidate_id": candidate_id, "currency": "USD", "canonical_symbol": "AAPL.US"},
        headers=write_headers("confirm-browser-tamper"),
    )
    accepted = await confirm(client, candidate_id, "confirm-server-state")

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert accepted.status_code == 201
    assert accepted.json()["canonical_symbol"] == "600519.SH"
    assert accepted.json()["currency"] == "CNY"


async def test_confirmation_replays_exact_result_after_database_restart(
    client: httpx.AsyncClient, database: Database
) -> None:
    candidate_id = await add_candidate(database)
    first = await confirm(client, candidate_id, "confirm-restart")
    await database.close()
    await database.start()

    replay = await confirm(client, candidate_id, "confirm-restart")

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()


async def test_confirmation_key_with_different_candidate_is_a_conflict(
    client: httpx.AsyncClient, database: Database
) -> None:
    first_id = await add_candidate(database)
    second_id = await add_candidate(
        database,
        canonical_symbol="000001.SZ",
        market="CN_SZ",
        currency="CNY",
    )

    first = await confirm(client, first_id, "confirm-body-conflict")
    conflict = await confirm(client, second_id, "confirm-body-conflict")

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("same_key", [True, False], ids=["replay", "single-consumer"])
async def test_concurrent_candidate_consumption_is_serialized(
    database: Database, same_key: bool
) -> None:
    candidate_id = await add_candidate(database)
    entered = asyncio.Event()
    release = asyncio.Event()
    second_started = asyncio.Event()
    first_app = app_for(database, HoldingInstrumentRepository(entered, release))
    second_app = app_for(database, SignalingClaimRepository(second_started))
    first_key = "concurrent-confirm-key"
    second_key = first_key if same_key else "concurrent-confirm-other"
    first_task = asyncio.create_task(confirm_for_app(first_app, candidate_id, first_key))
    await entered.wait()
    second_task = asyncio.create_task(confirm_for_app(second_app, candidate_id, second_key))
    await second_started.wait()
    try:
        await asyncio.sleep(0.05)
        assert not second_task.done()
    finally:
        release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first.status_code == 201
    if same_key:
        assert second.status_code == 201
        assert second.json() == first.json()
    else:
        assert second.status_code == 422
        assert second.json()["error"]["code"] == "INSTRUMENT_NOT_CONFIRMED"


async def test_confirmation_completion_failure_rolls_back_candidate_instrument_mapping_and_claim(
    database: Database,
) -> None:
    candidate_id = await add_candidate(database)
    key = "confirm-completion-failure"

    response = await confirm_for_app(app_for(database, CompletionFailureRepository()), candidate_id, key)
    async with database.session() as session:
        candidate = await session.get(InstrumentCandidateRow, candidate_id)
        instrument_count = await session.scalar(select(func.count()).select_from(InstrumentRow))
        mapping_count = await session.scalar(select(func.count()).select_from(InstrumentProviderSymbolRow))
        claim = await session.get(
            IdempotencyRow,
            ("POST:/api/v1/instruments/confirm", hash_idempotency_key(key)),
        )

    assert response.status_code == 500
    assert candidate is not None and candidate.consumed_at is None
    assert instrument_count == 0
    assert mapping_count == 0
    assert claim is None


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (DatabaseBusyError(), 503, "DATABASE_BUSY"),
        (DatabaseStartupError("DATABASE_STARTUP_FAILED"), 500, "PORTFOLIO_UNAVAILABLE"),
        (RuntimeError("private provider detail"), 500, "PORTFOLIO_UNAVAILABLE"),
    ],
)
async def test_confirmation_storage_failures_use_sanitized_errors(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    status: int,
    code: str,
) -> None:
    service = PortfolioService(database)

    async def fail_confirmation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise failure

    monkeypatch.setattr(service, "confirm_instrument", fail_confirmation)
    app = FastAPI()
    app.include_router(build_portfolio_router(service))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as failing_client:
        response = await confirm(failing_client, str(uuid4()), f"confirm-failure-{uuid4()}")

    assert response.status_code == status
    assert response.json() == {"error": {"code": code}}
    assert "private provider detail" not in response.text
