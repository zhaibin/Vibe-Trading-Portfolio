"""Fail-closed lifecycle management for the local portfolio SQLite database."""

import asyncio
import os
import sqlite3
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

DEFAULT_BUSY_TIMEOUT_MS = 500


class DatabaseStartupError(RuntimeError):
    """A local database cannot be safely started."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DatabaseBusyError(RuntimeError):
    """A bounded SQLite operation could not acquire its lock."""

    def __init__(self, code: str = "DATABASE_BUSY") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DatabaseUpgradeResult:
    backup_path: Path | None
    revision: str


def _is_busy(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        message = str(current).lower()
        if "database is locked" in message or "database is busy" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _raise_startup_or_busy(error: BaseException, code: str) -> None:
    if _is_busy(error):
        raise DatabaseBusyError() from error
    raise DatabaseStartupError(code) from error


def _set_owner_only_mode(path: Path, mode: int) -> None:
    os.chmod(path, mode)
    actual_mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    if actual_mode != mode:
        raise OSError(f"could not enforce mode {mode:o}")


def _assert_safe_path(path: Path) -> None:
    for component in (path.parent, *path.parent.parents):
        try:
            component_mode = os.lstat(component).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(component_mode):
            raise DatabaseStartupError("DATABASE_PATH_UNSAFE")
    try:
        path_mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(path_mode) or not stat.S_ISREG(path_mode):
        raise DatabaseStartupError("DATABASE_PATH_UNSAFE")


def validate_database_path(path: Path) -> None:
    """Create and enforce a private non-symlinked local storage location."""
    _assert_safe_path(path)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _assert_safe_path(path)
        _set_owner_only_mode(path.parent, 0o700)
    except DatabaseStartupError:
        raise
    except OSError as error:
        raise DatabaseStartupError("DATABASE_PATH_UNSAFE") from error


def _create_database_file(path: Path) -> None:
    if path.exists():
        return
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        _assert_safe_path(path)
        return
    except OSError as error:
        raise DatabaseStartupError("DATABASE_PATH_UNSAFE") from error
    else:
        os.close(descriptor)
    try:
        _assert_safe_path(path)
        _set_owner_only_mode(path, 0o600)
    except (DatabaseStartupError, OSError) as error:
        raise DatabaseStartupError("DATABASE_PATH_UNSAFE") from error


def _database_uri(path: Path) -> str:
    return f"file:{quote(path.absolute().as_posix())}?mode=rw"


def _open_connection(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    _assert_safe_path(path)
    connection = sqlite3.connect(_database_uri(path), uri=True, timeout=busy_timeout_ms / 1000)
    try:
        _assert_safe_path(path)
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection
    except BaseException:
        connection.close()
        raise


def _migrations_directory() -> Path:
    directory = resources.files("vibe_portfolio.portfolio").joinpath("migrations")
    path = Path(str(directory))
    if not path.is_dir():
        raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
    return path


def _configuration(path: Path, busy_timeout_ms: int, migrations_directory: Path | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(migrations_directory or _migrations_directory()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.absolute()}")
    config.attributes["portfolio_busy_timeout_ms"] = busy_timeout_ms
    return config


def _integrity_check(path: Path, busy_timeout_ms: int) -> None:
    try:
        with closing(_open_connection(path, busy_timeout_ms)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except DatabaseStartupError:
        raise
    except sqlite3.DatabaseError as error:
        _raise_startup_or_busy(error, "DATABASE_INTEGRITY_FAILED")
    if result != ("ok",):
        raise DatabaseStartupError("DATABASE_INTEGRITY_FAILED")


def _database_revision(path: Path, busy_timeout_ms: int) -> str | None:
    try:
        with closing(_open_connection(path, busy_timeout_ms)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
            ).fetchone()
            if table is None:
                return None
            revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except DatabaseStartupError:
        raise
    except sqlite3.DatabaseError as error:
        _raise_startup_or_busy(error, "DATABASE_SCHEMA_UNSUPPORTED")
    if len(revisions) != 1 or not isinstance(revisions[0][0], str):
        raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
    return revisions[0][0]


def _backup_path(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"{path.name}.backup-{timestamp}-{os.getpid()}.db")


def _copy_database(source: Path, destination: Path, busy_timeout_ms: int) -> None:
    _create_database_file(destination)
    with (
        closing(_open_connection(source, busy_timeout_ms)) as source_connection,
        closing(_open_connection(destination, busy_timeout_ms)) as destination_connection,
    ):
        source_connection.backup(destination_connection)
    _set_owner_only_mode(destination, 0o600)


def _restore_backup(backup_path: Path, path: Path, busy_timeout_ms: int, original_error: BaseException) -> None:
    try:
        _copy_database(backup_path, path, busy_timeout_ms)
    except BaseException:
        raise DatabaseStartupError("DATABASE_MIGRATION_FAILED") from original_error


def _upgrade_database_with_resources(
    path: Path, busy_timeout_ms: int, migrations_directory: Path
) -> DatabaseUpgradeResult:
    """Verify and migrate *path*, retaining a verified backup before a schema change."""
    validate_database_path(path)
    existed = path.exists()
    _create_database_file(path)
    try:
        _integrity_check(path, busy_timeout_ms)
        config = _configuration(path, busy_timeout_ms, migrations_directory)
        scripts = ScriptDirectory.from_config(config)
        head = scripts.get_current_head()
        if head is None:
            raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
        revision = _database_revision(path, busy_timeout_ms)
        known_revisions = {known_revision.revision for known_revision in scripts.walk_revisions()}
        if revision is not None and revision not in known_revisions:
            raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
        if revision == head:
            _set_owner_only_mode(path, 0o600)
            return DatabaseUpgradeResult(backup_path=None, revision=head)

        backup_path: Path | None = None
        if existed and path.stat().st_size > 0:
            backup_path = _backup_path(path)
            try:
                _copy_database(path, backup_path, busy_timeout_ms)
                _integrity_check(backup_path, busy_timeout_ms)
            except BaseException as error:
                _raise_startup_or_busy(error, "DATABASE_BACKUP_FAILED")

        try:
            command.upgrade(config, "head")
            _integrity_check(path, busy_timeout_ms)
            if _database_revision(path, busy_timeout_ms) != head:
                raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
        except BaseException as error:
            if backup_path is not None:
                _restore_backup(backup_path, path, busy_timeout_ms, error)
            if isinstance(error, (DatabaseStartupError, DatabaseBusyError)):
                raise
            _raise_startup_or_busy(error, "DATABASE_MIGRATION_FAILED")
    except (DatabaseStartupError, DatabaseBusyError):
        raise
    except BaseException as error:
        _raise_startup_or_busy(error, "DATABASE_STARTUP_FAILED")

    _set_owner_only_mode(path, 0o600)
    assert head is not None
    return DatabaseUpgradeResult(backup_path=backup_path, revision=head)


def upgrade_database(path: Path, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> DatabaseUpgradeResult:
    """Run the complete migration while a packaged resource is materialized."""
    migration_resource = resources.files("vibe_portfolio.portfolio").joinpath("migrations")
    with resources.as_file(migration_resource) as migrations_directory:
        return _upgrade_database_with_resources(path, busy_timeout_ms, migrations_directory)


def sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.absolute()}"


def enable_sqlite_pragmas(dbapi_connection: Any, _: object, *, busy_timeout_ms: int) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


class Database:
    """Own the async engine and sessions for the sidecar's local data only."""

    def __init__(self, path: Path, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        self.engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None

    async def start(self) -> None:
        await self.close()
        try:
            validate_database_path(self.path)
            migration = asyncio.create_task(asyncio.to_thread(upgrade_database, self.path, self.busy_timeout_ms))
            try:
                await asyncio.shield(migration)
            except asyncio.CancelledError:
                await asyncio.shield(migration)
                raise
            self.engine = create_async_engine(
                sqlite_async_url(self.path),
                pool_pre_ping=True,
                connect_args={"timeout": self.busy_timeout_ms / 1000},
            )
            event.listen(
                self.engine.sync_engine,
                "connect",
                lambda connection, record: enable_sqlite_pragmas(
                    connection, record, busy_timeout_ms=self.busy_timeout_ms
                ),
            )
            self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)
            async with self.engine.connect() as connection:
                await connection.execute(text("select 1"))
        except BaseException as error:
            await self.close()
            if isinstance(error, (DatabaseStartupError, DatabaseBusyError)):
                raise
            _raise_startup_or_busy(error, "DATABASE_STARTUP_FAILED")

    async def close(self) -> None:
        engine = self.engine
        self.engine = None
        self._sessions = None
        if engine is not None:
            disposal = asyncio.create_task(engine.dispose())
            try:
                await asyncio.shield(disposal)
            except asyncio.CancelledError:
                await asyncio.shield(disposal)
                raise

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._sessions is None:
            raise DatabaseStartupError("DATABASE_NOT_STARTED")
        try:
            async with self._sessions() as session:
                yield session
        except OperationalError as error:
            if _is_busy(error):
                raise DatabaseBusyError() from error
            raise
