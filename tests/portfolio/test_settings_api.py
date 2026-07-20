import os
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest_asyncio
from fastapi import FastAPI

from vibe_portfolio.config import Settings
from vibe_portfolio.market_data.models import InstrumentCandidate, ProviderInstrument, ProviderQuote
from vibe_portfolio.market_data.router import build_market_data_router
from vibe_portfolio.market_data.service import MarketDataService
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.tables import (
    InstrumentCandidateRow,
    InstrumentRow,
    LatestQuoteRow,
    QuoteRefreshRunRow,
)

NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


class StatusProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        return []

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        return []


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "settings.db")
    await database.start()
    try:
        yield database
    finally:
        await database.close()


async def test_settings_status_returns_only_redacted_relative_operational_metadata(
    database: Database,
) -> None:
    run_id = str(uuid4())
    empty_run_id = str(uuid4())
    instrument_id = str(uuid4())
    candidate_id = str(uuid4())
    async with database.session() as session, session.begin():
        session.add(
            InstrumentRow(
                id=instrument_id,
                canonical_symbol="600519.SH",
                name="Synthetic",
                market="CN_SH",
                currency="CNY",
                asset_type="equity",
                created_at=NOW - timedelta(days=1),
                updated_at=NOW - timedelta(days=1),
            )
        )
        session.add(
            QuoteRefreshRunRow(
                id=run_id,
                scope_hash="a" * 64,
                status="completed",
                started_at=NOW - timedelta(minutes=2),
                finished_at=NOW - timedelta(minutes=1),
                updated_count=1,
                stale_count=0,
                unavailable_count=0,
            )
        )
        session.add(
            QuoteRefreshRunRow(
                id=empty_run_id,
                scope_hash="b" * 64,
                status="completed",
                started_at=NOW - timedelta(seconds=1),
                finished_at=NOW,
                updated_count=0,
                stale_count=0,
                unavailable_count=0,
            )
        )
        await session.flush()
        session.add(
            LatestQuoteRow(
                instrument_id=instrument_id,
                price=Decimal("1.250000"),
                currency="CNY",
                provider="eastmoney",
                provider_symbol="1.600519",
                as_of=NOW - timedelta(minutes=3),
                fetched_at=NOW - timedelta(minutes=1),
                refresh_run_id=run_id,
            )
        )
        session.add(
            InstrumentCandidateRow(
                id=candidate_id,
                canonical_symbol="600519.SH",
                name="Synthetic",
                market="CN_SH",
                currency="CNY",
                asset_type="equity",
                provider="eastmoney",
                provider_symbols_json='[{"provider":"eastmoney","symbol":"1.600519"}]',
                created_at=NOW - timedelta(minutes=5),
                expires_at=NOW + timedelta(minutes=5),
                consumed_at=None,
            )
        )

    backup = database.path.with_name(f"{database.path.name}.backup-20260720T070000000000Z-1.db")
    backup.touch()
    backup_time = NOW - timedelta(hours=1)
    os.utime(backup, (backup_time.timestamp(), backup_time.timestamp()))
    settings = Settings(_env_file=None, database_path=Path("var/data/portfolio.db"))
    service = MarketDataService(
        database,
        [StatusProvider("eastmoney")],
        settings=settings,
        now=lambda: NOW,
    )
    app = FastAPI()
    app.include_router(build_market_data_router(service))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://sidecar",
    ) as client:
        response = await client.get("/api/v1/settings/status")

    assert response.status_code == 200
    assert response.json() == {
        "schema_revision": "20260719_0006",
        "migration_healthy": True,
        "database_path": "var/data/portfolio.db",
        "backup_directory": "var/data",
        "latest_backup_at": backup_time.isoformat().replace("+00:00", "Z"),
        "adapters": [
            {"name": "eastmoney", "enabled": True},
            {"name": "yahoo", "enabled": False},
            {"name": "tencent", "enabled": False},
        ],
        "last_successful_refresh_at": NOW.isoformat().replace("+00:00", "Z"),
        "last_refresh": {
            "status": "succeeded",
            "updated": 0,
            "stale": 0,
            "unavailable": 0,
            "finished_at": NOW.isoformat().replace("+00:00", "Z"),
        },
        "latest_quote_count": 1,
        "candidate_cache_count": 1,
    }
    payload = response.text.lower()
    assert str(database.path.parent).lower() not in payload
    for forbidden in ("http://", "https://", "token", "secret", "provider_symbol", "instrument_id"):
        assert forbidden not in payload
