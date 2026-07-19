"""Fail-closed lifecycle management for the local portfolio SQLite database."""

import asyncio
import os
import sqlite3
import stat
from collections.abc import AsyncIterator, Coroutine, Iterator
from contextlib import asynccontextmanager, closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import URL, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

DEFAULT_BUSY_TIMEOUT_MS = 500
T = TypeVar("T")


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


def _copy_traversable_tree(source: Traversable, destination: Path) -> None:
    if not source.is_dir():
        raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
    destination.mkdir()
    for child in source.iterdir():
        if child.name in {"", ".", ".."} or Path(child.name).name != child.name:
            raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")
        target = destination / child.name
        if child.is_dir():
            _copy_traversable_tree(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())
        else:
            raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED")


@contextmanager
def _materialized_migrations() -> Iterator[Path]:
    with TemporaryDirectory(prefix="vibe-portfolio-migrations-") as temporary_directory:
        try:
            migration_resource = resources.files("vibe_portfolio.portfolio").joinpath("migrations")
            materialized = Path(temporary_directory) / "migrations"
            _copy_traversable_tree(migration_resource, materialized)
        except DatabaseStartupError:
            raise
        except Exception as error:
            raise DatabaseStartupError("DATABASE_SCHEMA_UNSUPPORTED") from error
        yield materialized


def _sync_sqlite_url(path: Path) -> URL:
    return URL.create("sqlite", database=str(path.absolute()))


def _configuration(path: Path, busy_timeout_ms: int, migrations_directory: Path | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(migrations_directory or _migrations_directory()))
    sqlalchemy_url = _sync_sqlite_url(path)
    config.set_main_option(
        "sqlalchemy.url", sqlalchemy_url.render_as_string(hide_password=False).replace("%", "%%")
    )
    config.attributes["portfolio_sqlalchemy_url"] = sqlalchemy_url
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
            _set_owner_only_mode(path, 0o600)
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

    assert head is not None
    return DatabaseUpgradeResult(backup_path=backup_path, revision=head)


def upgrade_database(path: Path, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> DatabaseUpgradeResult:
    """Run the complete migration while a packaged resource is materialized."""
    with _materialized_migrations() as migrations_directory:
        return _upgrade_database_with_resources(path, busy_timeout_ms, migrations_directory)


def sqlite_async_url(path: Path) -> URL:
    return URL.create("sqlite+aiosqlite", database=str(path.absolute()))


def enable_sqlite_pragmas(dbapi_connection: Any, _: object, *, busy_timeout_ms: int) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


async def _wait_for_terminal(task: asyncio.Task[T]) -> T:
    """Wait until *task* is terminal while preserving the caller's first cancellation."""
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException:
            break

    try:
        result = task.result()
    except BaseException:
        if cancellation is not None:
            raise cancellation from None
        raise
    if cancellation is not None:
        raise cancellation
    return result


async def _run_to_terminal(awaitable: Coroutine[Any, Any, T]) -> T:
    return await _wait_for_terminal(asyncio.create_task(awaitable))


async def _verify_engine(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("select 1"))


async def _dispose_engine(engine: AsyncEngine) -> None:
    try:
        await _run_to_terminal(engine.dispose())
    except asyncio.CancelledError:
        raise
    except BaseException as error:
        raise DatabaseStartupError("DATABASE_SHUTDOWN_FAILED") from error


class Database:
    """Own the async engine and sessions for the sidecar's local data only."""

    def __init__(self, path: Path, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        self.engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            await self._close_locked()
            candidate: AsyncEngine | None = None
            sessions: async_sessionmaker[AsyncSession] | None = None
            try:
                validate_database_path(self.path)
                await _run_to_terminal(asyncio.to_thread(upgrade_database, self.path, self.busy_timeout_ms))
                candidate = create_async_engine(
                    sqlite_async_url(self.path),
                    pool_pre_ping=True,
                    connect_args={"timeout": self.busy_timeout_ms / 1000},
                )
                event.listen(
                    candidate.sync_engine,
                    "connect",
                    lambda connection, record: enable_sqlite_pragmas(
                        connection, record, busy_timeout_ms=self.busy_timeout_ms
                    ),
                )
                await _run_to_terminal(_verify_engine(candidate))
                sessions = async_sessionmaker(candidate, expire_on_commit=False)
            except BaseException as error:
                cleanup_error: BaseException | None = None
                if candidate is not None:
                    try:
                        await _dispose_engine(candidate)
                    except BaseException as caught_cleanup_error:
                        cleanup_error = caught_cleanup_error
                if isinstance(error, asyncio.CancelledError):
                    raise error from None
                if isinstance(cleanup_error, asyncio.CancelledError):
                    raise cleanup_error from None
                if isinstance(error, (DatabaseStartupError, DatabaseBusyError)):
                    raise
                _raise_startup_or_busy(error, "DATABASE_STARTUP_FAILED")

            assert candidate is not None
            assert sessions is not None
            self.engine = candidate
            self._sessions = sessions

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        engine = self.engine
        self.engine = None
        self._sessions = None
        if engine is not None:
            await _dispose_engine(engine)

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
