import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import text

from vibe_portfolio.portfolio import database as database_module
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
async def test_database_rejects_broken_symlink(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    path.symlink_to(tmp_path / "missing.db")

    with pytest.raises(DatabaseStartupError, match="DATABASE_PATH_UNSAFE"):
        await Database(path).start()


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
        connection.commit()

    with pytest.raises(DatabaseStartupError, match="DATABASE_SCHEMA_UNSUPPORTED") as raised:
        await Database(path).start()

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"


def test_upgrade_creates_verified_backup_before_schema_change(tmp_path: Path) -> None:
    path = legacy_database_fixture(tmp_path)

    result = upgrade_database(path)

    assert result.backup_path is not None
    assert result.backup_path.parent == path.parent
    assert result.backup_path.stat().st_mode & 0o777 == 0o600
    assert sqlite_integrity(result.backup_path) == "ok"
    with closing(sqlite3.connect(result.backup_path)) as connection:
        assert connection.execute("SELECT id FROM legacy_records").fetchone() == (1,)


def test_upgrade_restores_original_after_partial_migration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = legacy_database_fixture(tmp_path)

    def partially_mutate_then_fail(*_: object, **__: object) -> None:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("DROP TABLE legacy_records")
            connection.commit()
        raise RuntimeError("migration failed")

    monkeypatch.setattr("vibe_portfolio.portfolio.database.command.upgrade", partially_mutate_then_fail)

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


@pytest.mark.asyncio
async def test_database_start_maps_preflight_lock_to_busy(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    initialized = Database(path)
    await initialized.start()
    await initialized.close()
    lock = sqlite3.connect(path, timeout=0)
    try:
        assert lock.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        lock.execute("BEGIN EXCLUSIVE")
        lock.execute(
            "INSERT INTO accounts VALUES "
            "('held', 'Held', 'held', 'USD', NULL, 1, '2026-07-19T00:00:00+00:00', "
            "'2026-07-19T00:00:00+00:00', NULL)"
        )
        started = time.monotonic()
        with pytest.raises(DatabaseBusyError, match="DATABASE_BUSY"):
            await Database(path, busy_timeout_ms=50).start()
        assert time.monotonic() - started < 0.5
    finally:
        lock.rollback()
        lock.close()


@pytest.mark.asyncio
async def test_database_uses_packaged_migrations_outside_repository_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    database = Database(tmp_path / "runtime" / "portfolio.db")

    await database.start()
    await database.close()


@pytest.mark.asyncio
async def test_database_rejects_permission_changes_it_cannot_enforce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny_chmod(*_: object, **__: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("vibe_portfolio.portfolio.database.os.chmod", deny_chmod)

    with pytest.raises(DatabaseStartupError, match="DATABASE_PATH_UNSAFE"):
        await Database(tmp_path / "runtime" / "portfolio.db").start()


@pytest.mark.asyncio
async def test_database_revalidates_path_before_threaded_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "portfolio.db"
    replacement = tmp_path / "replacement.db"
    replacement.touch()
    original_validate = database_module.validate_database_path
    calls = 0

    def replace_after_first_validation(candidate: Path) -> None:
        nonlocal calls
        original_validate(candidate)
        calls += 1
        if calls == 1:
            path.symlink_to(replacement)

    monkeypatch.setattr("vibe_portfolio.portfolio.database.validate_database_path", replace_after_first_validation)

    with pytest.raises(DatabaseStartupError, match="DATABASE_PATH_UNSAFE"):
        await Database(path).start()


@pytest.mark.asyncio
async def test_database_clears_sessions_after_engine_start_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "portfolio.db")

    def fail_ping(*_: object, **__: object) -> object:
        raise RuntimeError("ping failed")

    monkeypatch.setattr("vibe_portfolio.portfolio.database.text", fail_ping)

    with pytest.raises(DatabaseStartupError, match="DATABASE_STARTUP_FAILED"):
        await database.start()

    assert database.engine is None
    with pytest.raises(DatabaseStartupError, match="DATABASE_NOT_STARTED"):
        async with database.session():
            pass


@pytest.mark.asyncio
async def test_database_replaces_engine_on_repeated_start(tmp_path: Path) -> None:
    database = Database(tmp_path / "portfolio.db")
    await database.start()
    first_engine = database.engine

    await database.start()
    try:
        assert database.engine is not first_engine
        async with database.session() as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await database.close()
