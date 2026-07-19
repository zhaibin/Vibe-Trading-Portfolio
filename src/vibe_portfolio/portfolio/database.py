"""Fail-closed lifecycle management for the local portfolio SQLite database."""

import asyncio
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


def _set_owner_only_mode(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        # Windows and some mounted filesystems do not support POSIX permissions.
        pass


def validate_database_path(path: Path) -> None:
    """Create a private parent directory and reject symlink traversal."""
    if path.is_symlink() or any(parent.is_symlink() for parent in (path.parent, *path.parent.parents)):
        raise DatabaseStartupError("DATABASE_PATH_UNSAFE")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _set_owner_only_mode(path.parent, 0o700)
    except OSError as error:
        raise DatabaseStartupError("DATABASE_PATH_UNSAFE") from error
    if path.exists() and not path.is_file():
        raise DatabaseStartupError("DATABASE_PATH_UNSAFE")


def _configuration(path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


def _integrity_check(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as error:
        raise DatabaseStartupError("DATABASE_INTEGRITY_FAILED") from error
    if result != ("ok",):
        raise DatabaseStartupError("DATABASE_INTEGRITY_FAILED")


def _database_revision(path: Path) -> str | None:
    with closing(sqlite3.connect(path)) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        if table is None:
            return None
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(revisions) != 1 or not isinstance(revisions[0][0], str):
        raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
    return revisions[0][0]


def _backup_path(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"{path.name}.backup-{timestamp}-{os.getpid()}.db")


def _copy_database(source: Path, destination: Path) -> None:
    with (
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(destination)) as destination_connection,
    ):
        source_connection.backup(destination_connection)


def _is_busy(error: BaseException) -> bool:
    message = str(error).lower()
    return "database is locked" in message or "database is busy" in message


def upgrade_database(path: Path) -> DatabaseUpgradeResult:
    """Verify and migrate *path*, retaining a verified backup before a schema change."""
    validate_database_path(path)
    existed = path.exists()
    _integrity_check(path)
    config = _configuration(path)
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
    revision = _database_revision(path)
    known_revisions = {revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()}
    if revision is not None and revision not in known_revisions:
        raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
    if revision == head:
        _set_owner_only_mode(path, 0o600)
        return DatabaseUpgradeResult(backup_path=None, revision=head)

    backup_path: Path | None = None
    if existed and path.stat().st_size > 0:
        backup_path = _backup_path(path)
        try:
            _copy_database(path, backup_path)
            _set_owner_only_mode(backup_path, 0o600)
            _integrity_check(backup_path)
        except (OSError, sqlite3.DatabaseError, DatabaseStartupError) as error:
            raise DatabaseStartupError("DATABASE_BACKUP_FAILED") from error

    try:
        command.upgrade(config, "head")
        _integrity_check(path)
        if _database_revision(path) != head:
            raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
    except DatabaseStartupError:
        if backup_path is not None:
            _copy_database(backup_path, path)
        raise
    except Exception as error:
        if backup_path is not None:
            try:
                _copy_database(backup_path, path)
            except (OSError, sqlite3.DatabaseError) as restore_error:
                raise DatabaseStartupError("DATABASE_MIGRATION_FAILED") from restore_error
        if _is_busy(error):
            raise DatabaseBusyError() from error
        raise DatabaseStartupError("DATABASE_MIGRATION_FAILED") from error

    _set_owner_only_mode(path, 0o600)
    return DatabaseUpgradeResult(backup_path=backup_path, revision=head)


def sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve()}"


def enable_sqlite_pragmas(dbapi_connection: Any, _: object, *, busy_timeout_ms: int) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
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
        validate_database_path(self.path)
        await asyncio.to_thread(upgrade_database, self.path)
        self.engine = create_async_engine(sqlite_async_url(self.path), pool_pre_ping=True)
        event.listen(
            self.engine.sync_engine,
            "connect",
            lambda connection, record: enable_sqlite_pragmas(connection, record, busy_timeout_ms=self.busy_timeout_ms),
        )
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("select 1"))
        except OperationalError as error:
            await self.close()
            if _is_busy(error):
                raise DatabaseBusyError() from error
            raise DatabaseStartupError("DATABASE_STARTUP_FAILED") from error

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
        self.engine = None
        self._sessions = None

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
