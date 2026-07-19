import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_initial_migration_creates_all_snapshot_tables(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    assert {
        "accounts",
        "instruments",
        "instrument_provider_symbols",
        "positions",
        "latest_quotes",
        "quote_refresh_runs",
        "quote_refresh_items",
        "instrument_candidates",
        "idempotency_records",
    } <= tables
