import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import text

from vibe_portfolio.portfolio.database import Database, DatabaseBusyError, DatabaseStartupError, upgrade_database


def sqlite_integrity(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def legacy_database_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "portfolio.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE legacy_records (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO legacy_records VALUES (1)")
        connection.commit()
    return path


@pytest.mark.asyncio
async def test_database_creates_owner_only_parent_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "portfolio.db"
    database = Database(path)

    await database.start()
    try:
        assert path.exists()
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
        async with database.session() as session:
            revision = await session.execute(text("SELECT version_num FROM alembic_version"))
            assert revision.scalar_one() == "20260719_0001"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_database_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    real.touch()
    link = tmp_path / "linked.db"
    link.symlink_to(real)

    with pytest.raises(DatabaseStartupError, match="DATABASE_PATH_UNSAFE") as raised:
        await Database(link).start()

    assert raised.value.code == "DATABASE_PATH_UNSAFE"


@pytest.mark.asyncio
async def test_database_rejects_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    path.write_bytes(b"not a sqlite database")

    with pytest.raises(DatabaseStartupError, match="DATABASE_INTEGRITY_FAILED") as raised:
        await Database(path).start()

    assert raised.value.code == "DATABASE_INTEGRITY_FAILED"


@pytest.mark.asyncio
async def test_database_rejects_future_revision(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('20990101_9999')")

    with pytest.raises(DatabaseStartupError, match="DATABASE_SCHEMA_UNSUPPORTED") as raised:
        await Database(path).start()

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"


def test_upgrade_creates_verified_backup_before_schema_change(tmp_path: Path) -> None:
    path = legacy_database_fixture(tmp_path)

    result = upgrade_database(path)

    assert result.backup_path is not None
    assert result.backup_path.parent == path.parent
    assert sqlite_integrity(result.backup_path) == "ok"
    with closing(sqlite3.connect(result.backup_path)) as connection:
        assert connection.execute("SELECT id FROM legacy_records").fetchone() == (1,)


def test_upgrade_preserves_backup_when_migration_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = legacy_database_fixture(tmp_path)

    def fail_upgrade(*_: object, **__: object) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr("vibe_portfolio.portfolio.database.command.upgrade", fail_upgrade)

    with pytest.raises(DatabaseStartupError, match="DATABASE_MIGRATION_FAILED"):
        upgrade_database(path)

    backups = list(tmp_path.glob("portfolio.db.backup-*.db"))
    assert len(backups) == 1
    assert sqlite_integrity(backups[0]) == "ok"
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT id FROM legacy_records").fetchone() == (1,)


@pytest.mark.asyncio
async def test_database_connections_enforce_pragmas_and_bounded_busy_failure(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    database = Database(path, busy_timeout_ms=500)
    await database.start()
    lock = sqlite3.connect(path)
    try:
        async with database.session() as session:
            assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            assert (await session.execute(text("PRAGMA journal_mode"))).scalar_one() == "wal"
            assert (await session.execute(text("PRAGMA busy_timeout"))).scalar_one() == 500

        lock.execute("BEGIN EXCLUSIVE")
        lock.execute(
            "INSERT INTO accounts VALUES "
            "('account-1', 'Main', 'main', 'USD', NULL, 1, '2026-07-19T00:00:00+00:00', "
            "'2026-07-19T00:00:00+00:00', NULL)"
        )
        with pytest.raises(DatabaseBusyError, match="DATABASE_BUSY") as raised:
            async with database.session() as session:
                await session.execute(text(
                    "INSERT INTO accounts VALUES "
                    "('account-2', 'Other', 'other', 'USD', NULL, 1, '2026-07-19T00:00:00+00:00', "
                    "'2026-07-19T00:00:00+00:00', NULL)"
                ))
                await session.commit()
        assert raised.value.code == "DATABASE_BUSY"
    finally:
        lock.rollback()
        lock.close()
        await database.close()
