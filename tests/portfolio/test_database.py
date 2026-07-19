import asyncio
import os
import sqlite3
import threading
import time
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import text

from vibe_portfolio.portfolio import database as database_module
from vibe_portfolio.portfolio.database import (
    Database,
    DatabaseBusyError,
    DatabaseStartupError,
    _configuration,
    _wait_for_terminal,
    sqlite_async_url,
    upgrade_database,
)

MIGRATION_DIRECTORY = Path("src/vibe_portfolio/portfolio/migrations").absolute()


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


class ControlledEngine:
    def __init__(self, *, dispose_error: BaseException | None = None) -> None:
        self.dispose_started = asyncio.Event()
        self.dispose_release = asyncio.Event()
        self.dispose_finished = False
        self.dispose_error = dispose_error

    async def dispose(self) -> None:
        self.dispose_started.set()
        try:
            await self.dispose_release.wait()
            if self.dispose_error is not None:
                raise self.dispose_error
        finally:
            self.dispose_finished = True


@pytest.mark.asyncio
async def test_terminal_wait_preserves_first_cancellation_until_failure_is_terminal() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def fail_after_release() -> None:
        nonlocal finished
        started.set()
        try:
            await release.wait()
            raise RuntimeError("inner failure")
        finally:
            finished = True

    inner = asyncio.create_task(fail_after_release())
    waiter = asyncio.create_task(_wait_for_terminal(inner))
    await started.wait()
    waiter.cancel("first cancellation")
    await asyncio.sleep(0)
    waiter.cancel("second cancellation")
    await asyncio.sleep(0)

    assert not waiter.done()
    assert not inner.done()
    release.set()
    with pytest.raises(asyncio.CancelledError, match="first cancellation"):
        await waiter

    assert inner.done()
    assert finished


@pytest.mark.asyncio
async def test_database_waits_for_failed_migration_after_repeated_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration_started = threading.Event()
    migration_release = threading.Event()
    migration_finished = threading.Event()

    def fail_migration(*_: object) -> None:
        migration_started.set()
        migration_release.wait(timeout=5)
        migration_finished.set()
        raise RuntimeError("migration failed after cancellation")

    monkeypatch.setattr(database_module, "upgrade_database", fail_migration)
    database = Database(tmp_path / "portfolio.db")
    start = asyncio.create_task(database.start())
    assert await asyncio.to_thread(migration_started.wait, 1)

    start.cancel("first cancellation")
    await asyncio.sleep(0)
    start.cancel("second cancellation")
    await asyncio.sleep(0)
    assert not start.done()
    migration_release.set()

    with pytest.raises(asyncio.CancelledError, match="first cancellation"):
        await start
    assert migration_finished.is_set()
    assert database.engine is None


@pytest.mark.asyncio
async def test_database_ping_cancellation_waits_for_ping_and_disposal_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ping_started = asyncio.Event()
    ping_release = asyncio.Event()
    ping_finished = False
    engine = ControlledEngine(dispose_error=RuntimeError("dispose failed"))

    class ControlledConnection:
        async def __aenter__(self) -> "ControlledConnection":
            nonlocal ping_finished
            ping_started.set()
            try:
                await ping_release.wait()
                raise RuntimeError("ping failed")
            finally:
                ping_finished = True

        async def __aexit__(self, *_: object) -> None:
            return None

    engine.sync_engine = object()
    engine.connect = lambda: ControlledConnection()
    monkeypatch.setattr(database_module, "upgrade_database", lambda *_: None)
    monkeypatch.setattr(database_module, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(database_module.event, "listen", lambda *_args, **_kwargs: None)
    database = Database(tmp_path / "portfolio.db")
    start = asyncio.create_task(database.start())
    await ping_started.wait()
    assert database.engine is None
    assert database._sessions is None

    start.cancel("ping cancellation")
    await asyncio.sleep(0)
    assert not start.done()
    ping_release.set()
    await engine.dispose_started.wait()
    engine.dispose_release.set()

    with pytest.raises(asyncio.CancelledError, match="ping cancellation"):
        await start
    assert ping_finished
    assert engine.dispose_finished
    assert database.engine is None


@pytest.mark.asyncio
async def test_database_does_not_publish_engine_when_session_factory_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "portfolio.db")

    def fail_session_factory(*_: object, **__: object) -> Any:
        raise RuntimeError("session factory failed")

    monkeypatch.setattr(database_module, "async_sessionmaker", fail_session_factory)

    with pytest.raises(DatabaseStartupError, match="DATABASE_STARTUP_FAILED"):
        await database.start()

    assert database.engine is None
    assert database._sessions is None


@pytest.mark.asyncio
async def test_database_close_waits_through_repeated_cancellation_and_disposal_failure(tmp_path: Path) -> None:
    engine = ControlledEngine(dispose_error=RuntimeError("dispose failed"))
    database = Database(tmp_path / "portfolio.db")
    database.engine = cast(Any, engine)
    database._sessions = cast(Any, object())
    close = asyncio.create_task(database.close())
    await engine.dispose_started.wait()

    close.cancel("first close cancellation")
    await asyncio.sleep(0)
    close.cancel("second close cancellation")
    await asyncio.sleep(0)
    assert not close.done()
    engine.dispose_release.set()

    with pytest.raises(asyncio.CancelledError, match="first close cancellation"):
        await close
    assert engine.dispose_finished
    assert database.engine is None
    assert database._sessions is None


@pytest.mark.asyncio
async def test_database_close_maps_uncancelled_disposal_failure_to_stable_error(tmp_path: Path) -> None:
    engine = ControlledEngine(dispose_error=RuntimeError("dispose failed"))
    engine.dispose_release.set()
    database = Database(tmp_path / "portfolio.db")
    database.engine = cast(Any, engine)

    with pytest.raises(DatabaseStartupError, match="DATABASE_SHUTDOWN_FAILED") as raised:
        await database.close()

    assert raised.value.code == "DATABASE_SHUTDOWN_FAILED"
    assert engine.dispose_finished
    assert database.engine is None


@pytest.mark.asyncio
async def test_concurrent_close_cannot_be_overtaken_by_start_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration_started = threading.Event()
    migration_release = threading.Event()
    real_upgrade = database_module.upgrade_database

    def delayed_upgrade(*args: object) -> object:
        migration_started.set()
        migration_release.wait(timeout=5)
        return real_upgrade(*args)

    monkeypatch.setattr(database_module, "upgrade_database", delayed_upgrade)
    database = Database(tmp_path / "portfolio.db")
    start = asyncio.create_task(database.start())
    assert await asyncio.to_thread(migration_started.wait, 1)
    close = asyncio.create_task(database.close())
    await asyncio.sleep(0)
    migration_release.set()

    await asyncio.gather(start, close)
    assert database.engine is None
    assert database._sessions is None
    with pytest.raises(DatabaseStartupError, match="DATABASE_NOT_STARTED"):
        async with database.session():
            pass


def test_upgrade_materializes_zip_backed_migration_tree_for_whole_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "resources.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("portfolio/migrations/env.py", "zip env")
        archive.writestr("portfolio/migrations/script.py.mako", "zip template")
        archive.writestr("portfolio/migrations/versions/revision.py", "zip revision")
    archive = zipfile.ZipFile(archive_path)
    resource_root = zipfile.Path(archive, "portfolio/")
    observed_directory: Path | None = None

    def inspect_materialized_tree(path: Path, _: int, migrations_directory: Path) -> str:
        nonlocal observed_directory
        observed_directory = migrations_directory
        assert migrations_directory.is_dir()
        assert (migrations_directory / "env.py").read_text() == "zip env"
        assert (migrations_directory / "script.py.mako").read_text() == "zip template"
        assert (migrations_directory / "versions" / "revision.py").read_text() == "zip revision"
        return "materialized"

    monkeypatch.setattr(database_module.resources, "files", lambda _: resource_root)
    monkeypatch.setattr(database_module, "_upgrade_database_with_resources", inspect_materialized_tree)
    try:
        assert upgrade_database(tmp_path / "portfolio.db") == "materialized"
        assert observed_directory is not None
        assert not observed_directory.exists()
    finally:
        archive.close()


def test_upgrade_runs_real_alembic_migration_from_zip_backed_canonical_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "canonical-resources.zip"
    resource_paths = (
        Path("env.py"),
        Path("script.py.mako"),
        Path("versions/20260719_0001_portfolio_snapshot.py"),
    )
    with zipfile.ZipFile(archive_path, "w") as writer:
        for relative_path in resource_paths:
            writer.writestr(
                f"portfolio/migrations/{relative_path.as_posix()}",
                (MIGRATION_DIRECTORY / relative_path).read_bytes(),
            )

    archive = zipfile.ZipFile(archive_path)
    resource_root = zipfile.Path(archive, "portfolio/")
    monkeypatch.setattr(database_module.resources, "files", lambda _: resource_root)
    path = tmp_path / "zip-backed? #%.db"
    try:
        result = upgrade_database(path)
    finally:
        archive.close()

    assert result.revision == "20260719_0001"
    assert path.is_file()
    assert not (tmp_path / "zip-backed").exists()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("20260719_0001",)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'accounts'"
        ).fetchone() == (1,)


def test_upgrade_maps_invalid_packaged_resource_to_stable_schema_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration_file = tmp_path / "not-a-directory"
    migration_file.write_text("invalid")
    monkeypatch.setattr(database_module.resources, "files", lambda _: migration_file.parent)

    with pytest.raises(DatabaseStartupError, match="DATABASE_SCHEMA_UNSUPPORTED") as raised:
        upgrade_database(tmp_path / "portfolio.db")

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"


def test_upgrade_maps_packaged_resource_lookup_failure_to_stable_schema_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_lookup(_: str) -> Any:
        raise FileNotFoundError("package resources unavailable")

    monkeypatch.setattr(database_module.resources, "files", fail_lookup)

    with pytest.raises(DatabaseStartupError, match="DATABASE_SCHEMA_UNSUPPORTED") as raised:
        upgrade_database(tmp_path / "portfolio.db")

    assert raised.value.code == "DATABASE_SCHEMA_UNSUPPORTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["portfolio?one.db", "portfolio#one.db", "portfolio%one.db", "portfolio one.db"])
async def test_database_urls_address_exact_special_character_path(tmp_path: Path, filename: str) -> None:
    path = tmp_path / filename
    configuration = _configuration(path, 50, MIGRATION_DIRECTORY)
    alembic_url = configuration.attributes["portfolio_sqlalchemy_url"]

    assert alembic_url.database == str(path.absolute())
    assert sqlite_async_url(path).database == str(path.absolute())
    database = Database(path)
    await database.start()
    try:
        assert path.exists()
        async with database.session() as session:
            assert (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == (
                "20260719_0001"
            )
    finally:
        await database.close()


def test_upgrade_restores_original_when_final_permission_enforcement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = legacy_database_fixture(tmp_path)
    real_set_mode = database_module._set_owner_only_mode
    path_mode_calls = 0

    def fail_first_database_mode(candidate: Path, mode: int) -> None:
        nonlocal path_mode_calls
        if candidate == path:
            path_mode_calls += 1
            if path_mode_calls == 1:
                raise PermissionError("final chmod denied")
        real_set_mode(candidate, mode)

    monkeypatch.setattr(database_module, "_set_owner_only_mode", fail_first_database_mode)

    with pytest.raises(DatabaseStartupError, match="DATABASE_MIGRATION_FAILED") as raised:
        upgrade_database(path)

    assert raised.value.code == "DATABASE_MIGRATION_FAILED"
    assert path_mode_calls == 2
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT id FROM legacy_records").fetchone() == (1,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone() is None


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


def test_upgrade_maps_lstat_permission_failure_to_path_unsafe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "portfolio.db"
    real_lstat = os.lstat

    def deny_inspection(candidate: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if Path(candidate) == path and not args and not kwargs:
            raise PermissionError("lstat denied")
        return real_lstat(candidate, *args, **kwargs)

    monkeypatch.setattr(database_module.os, "lstat", deny_inspection)

    with pytest.raises(DatabaseStartupError, match="DATABASE_PATH_UNSAFE") as raised:
        upgrade_database(path)

    assert raised.value.code == "DATABASE_PATH_UNSAFE"


@pytest.mark.asyncio
async def test_database_start_maps_lstat_permission_failure_to_path_unsafe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "portfolio.db"
    real_lstat = os.lstat

    def deny_inspection(candidate: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if Path(candidate) == path and not args and not kwargs:
            raise PermissionError("lstat denied")
        return real_lstat(candidate, *args, **kwargs)

    monkeypatch.setattr(database_module.os, "lstat", deny_inspection)
    database = Database(path)

    with pytest.raises(DatabaseStartupError, match="DATABASE_PATH_UNSAFE") as raised:
        await database.start()

    assert raised.value.code == "DATABASE_PATH_UNSAFE"
    assert database.engine is None
    assert database._sessions is None


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
