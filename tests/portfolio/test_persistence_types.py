from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from vibe_portfolio.portfolio.persistence_types import ExactDecimal, UtcIsoDateTime
from vibe_portfolio.portfolio.tables import AccountRow, Base, InstrumentRow, PositionRow


async def test_exact_decimal_is_stored_as_text(async_engine: AsyncEngine) -> None:
    async with async_engine.begin() as connection:
        table = Table("exact_values", MetaData(), Column("value", ExactDecimal(), nullable=False))
        await connection.run_sync(table.create)
        await connection.execute(table.insert().values(value=Decimal("1.230000")))
        stored = (await connection.execute(text("select typeof(value), value from exact_values"))).one()
    assert stored == ("text", "1.230000")


async def test_utc_datetime_round_trip_returns_aware_utc(async_engine: AsyncEngine) -> None:
    async with async_engine.begin() as connection:
        table = Table("timestamp_values", MetaData(), Column("value", UtcIsoDateTime(), nullable=False))
        await connection.run_sync(table.create)
        await connection.execute(table.insert().values(value=datetime(2026, 7, 19, 12, tzinfo=UTC)))
        returned = (await connection.execute(table.select())).scalar_one()
    assert returned == datetime(2026, 7, 19, 12, tzinfo=UTC)
    assert returned.tzinfo is UTC


async def test_foreign_keys_are_enabled(async_engine: AsyncEngine) -> None:
    async with async_engine.connect() as connection:
        assert (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1


async def test_active_position_unique_index_allows_archived_duplicate(async_engine: AsyncEngine) -> None:
    account_id = str(uuid4())
    instrument_id = str(uuid4())
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            AccountRow.__table__.insert().values(
                id=account_id,
                name="Main",
                normalized_name="main",
                currency="USD",
                version=1,
                created_at=datetime(2026, 7, 19, tzinfo=UTC),
                updated_at=datetime(2026, 7, 19, tzinfo=UTC),
            )
        )
        await connection.execute(
            InstrumentRow.__table__.insert().values(
                id=instrument_id,
                canonical_symbol="ACME.US",
                name="Acme",
                market="US",
                currency="USD",
                asset_type="equity",
                created_at=datetime(2026, 7, 19, tzinfo=UTC),
                updated_at=datetime(2026, 7, 19, tzinfo=UTC),
            )
        )
        active = dict(
            id=str(uuid4()),
            account_id=account_id,
            instrument_id=instrument_id,
            quantity=Decimal("1"),
            average_cost=Decimal("2"),
            version=1,
            created_at=datetime(2026, 7, 19, tzinfo=UTC),
            updated_at=datetime(2026, 7, 19, tzinfo=UTC),
        )
        await connection.execute(PositionRow.__table__.insert().values(**active))
        duplicate = {**active, "id": str(uuid4())}
        with pytest.raises(IntegrityError):
            await connection.execute(PositionRow.__table__.insert().values(**duplicate))
        await connection.execute(
            PositionRow.__table__.update()
            .where(PositionRow.id == active["id"])
            .values(archived_at=datetime(2026, 7, 19, 1, tzinfo=UTC))
        )
        await connection.execute(PositionRow.__table__.insert().values(**duplicate))
