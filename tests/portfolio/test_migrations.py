import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from vibe_portfolio.portfolio.database import _configuration
from vibe_portfolio.portfolio.tables import (
    AccountVersionRow,
    IdempotencyRow,
    PositionVersionRow,
    QuoteRefreshRunRow,
)


def _upgrade_database(path: Path, revision: str = "head") -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, revision)


def test_initial_migration_creates_all_snapshot_tables(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    _upgrade_database(path)
    with closing(sqlite3.connect(path)) as connection:
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
        "account_versions",
        "position_versions",
    } <= tables


def test_initial_migration_has_revision_and_material_schema_behavior(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    _upgrade_database(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("select version_num from alembic_version").fetchone() == ("20260719_0004",)
        foreign_keys = connection.execute("PRAGMA foreign_key_list(positions)").fetchall()
        assert {foreign_key[2] for foreign_key in foreign_keys} == {"accounts", "instruments"}
        indexes = connection.execute("PRAGMA index_list(positions)").fetchall()
        active_index = next(index for index in indexes if index[1] == "uq_positions_active_account_instrument")
        assert active_index[2] == 1
        index_sql = connection.execute(
            "select sql from sqlite_master where type='index' and name='uq_positions_active_account_instrument'"
        ).fetchone()
        assert index_sql is not None and "WHERE archived_at IS NULL" in index_sql[0]

        connection.execute(
            "insert into accounts values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "account-1",
                "Main",
                "main",
                "USD",
                None,
                1,
                "2026-07-19T00:00:00+00:00",
                "2026-07-19T00:00:00+00:00",
                None,
            ),
        )
        connection.execute(
            "insert into instruments values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "instrument-1",
                "ACME.US",
                "Acme",
                "US",
                "USD",
                "equity",
                "2026-07-19T00:00:00+00:00",
                "2026-07-19T00:00:00+00:00",
            ),
        )
        position = (
            "position-1",
            "account-1",
            "instrument-1",
            "1",
            "2",
            None,
            1,
            "2026-07-19T00:00:00+00:00",
            "2026-07-19T00:00:00+00:00",
            None,
        )
        connection.execute("insert into positions values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", position)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "insert into positions values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("position-2", *position[1:])
            )
        connection.execute(
            "update positions set archived_at = ? where id = ?", ("2026-07-19T01:00:00+00:00", "position-1")
        )
        connection.execute("insert into positions values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("position-2", *position[1:]))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "insert into instruments values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "instrument-2",
                    "BAD.US",
                    "Bad",
                    "US",
                    "EUR",
                    "equity",
                    "2026-07-19T00:00:00+00:00",
                    "2026-07-19T00:00:00+00:00",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "insert into positions values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "position-3",
                    "missing-account",
                    "instrument-1",
                    "1",
                    "2",
                    None,
                    1,
                    "2026-07-19T00:00:00+00:00",
                    "2026-07-19T00:00:00+00:00",
                    None,
                ),
            )
        connection.execute(
            "insert into quote_refresh_runs (id, scope_hash, status, started_at) values (?, ?, ?, ?)",
            ("run-1", "a" * 64, "running", "2026-07-19T00:00:00+00:00"),
        )
        assert connection.execute(
            "select updated_count, stale_count, unavailable_count from quote_refresh_runs where id = ?", ("run-1",)
        ).fetchone() == (0, 0, 0)


def test_quote_refresh_run_metadata_matches_migration_server_defaults() -> None:
    for column_name in ("updated_count", "stale_count", "unavailable_count"):
        server_default = QuoteRefreshRunRow.__table__.c[column_name].server_default
        assert server_default is not None
        assert str(server_default.arg) == "0"


def test_root_and_runtime_use_the_same_explicit_migration_revision(tmp_path: Path) -> None:
    root_config = Config("alembic.ini")
    runtime_config = _configuration(tmp_path / "portfolio.db", 50)

    root_script = ScriptDirectory.from_config(root_config)
    runtime_script = ScriptDirectory.from_config(runtime_config)
    assert root_script.dir == runtime_script.dir
    revision = next(runtime_script.walk_revisions())
    assert revision.revision == "20260719_0004"
    assert revision.path is not None
    migration_source = Path(revision.path).read_text()
    assert "position_versions" in migration_source


def test_privacy_migration_moves_valid_replay_state_to_history_and_drops_response_json(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    _upgrade_database(path, "20260719_0002")
    account_id = "11111111-1111-4111-8111-111111111111"
    original = {
        "id": account_id,
        "name": "迁移前名称",
        "currency": "CNY",
        "cash_balance": "7.500000",
        "version": 1,
        "created_at": "2026-07-19T00:00:00Z",
        "updated_at": "2026-07-19T00:00:00Z",
        "archived_at": None,
    }
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "insert into accounts values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                "当前名称",
                "当前名称",
                "CNY",
                "8.000000",
                2,
                "2026-07-19T00:00:00Z",
                "2026-07-19T01:00:00Z",
                None,
            ),
        )
        connection.execute(
            "insert into idempotency_records values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "POST:/api/v1/accounts",
                "a" * 64,
                "b" * 64,
                "completed",
                account_id,
                201,
                "2026-07-19T00:00:00Z",
                "2026-07-20T00:00:00Z",
                json.dumps(original, ensure_ascii=False),
            ),
        )
        connection.commit()

    _upgrade_database(path)

    with closing(sqlite3.connect(path)) as connection:
        idempotency_columns = {
            row[1] for row in connection.execute("pragma table_info(idempotency_records)").fetchall()
        }
        histories = connection.execute(
            "select version, name, cash_balance from account_versions where account_id = ? order by version",
            (account_id,),
        ).fetchall()
        replay_metadata = connection.execute(
            "select resource_id, resource_version, response_status from idempotency_records"
        ).fetchone()

    assert "response_json" not in idempotency_columns
    assert "resource_version" in idempotency_columns
    assert histories == [(1, "迁移前名称", "7.500000"), (2, "当前名称", "8.000000")]
    assert replay_metadata == (account_id, 1, 201)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("cash_balance", "-1"),
        ("cash_balance", "1.0000001"),
        ("created_at", "2026-07-19T00:00:00"),
        ("updated_at", "2026-07-19T00:00:00"),
    ],
    ids=["negative-money", "overprecision-money", "naive-created-at", "naive-updated-at"],
)
def test_privacy_migration_skips_parseable_invalid_completed_snapshot(
    tmp_path: Path, field: str, invalid_value: str
) -> None:
    path = tmp_path / "portfolio.db"
    _upgrade_database(path, "20260719_0002")
    account_id = "22222222-2222-4222-8222-222222222222"
    snapshot: dict[str, object] = {
        "id": account_id,
        "name": "无效迁移历史",
        "currency": "CNY",
        "cash_balance": "1.000000",
        "version": 1,
        "created_at": "2026-07-19T00:00:00Z",
        "updated_at": "2026-07-19T00:00:00Z",
        "archived_at": None,
    }
    snapshot[field] = invalid_value
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "insert into accounts values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                "当前有效状态",
                "当前有效状态",
                "CNY",
                "2.000000",
                2,
                "2026-07-19T00:00:00Z",
                "2026-07-19T01:00:00Z",
                None,
            ),
        )
        connection.execute(
            "insert into idempotency_records values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "POST:/api/v1/accounts",
                "c" * 64,
                "d" * 64,
                "completed",
                account_id,
                201,
                "2026-07-19T00:00:00Z",
                "2026-07-20T00:00:00Z",
                json.dumps(snapshot, ensure_ascii=False),
            ),
        )
        connection.commit()

    _upgrade_database(path)

    with closing(sqlite3.connect(path)) as connection:
        histories = connection.execute(
            "select version, cash_balance, created_at, updated_at "
            "from account_versions where account_id = ? order by version",
            (account_id,),
        ).fetchall()
        replay_version = connection.execute(
            "select resource_version from idempotency_records where key_hash = ?", ("c" * 64,)
        ).fetchone()

    assert histories == [(2, "2.000000", "2026-07-19T00:00:00Z", "2026-07-19T01:00:00Z")]
    assert replay_version == (None,)


def test_migrated_schema_matches_idempotency_and_account_history_orm_metadata(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    _upgrade_database(path)
    with closing(sqlite3.connect(path)) as connection:
        idempotency_columns = {
            row[1] for row in connection.execute("pragma table_info(idempotency_records)").fetchall()
        }
        history_columns = {row[1] for row in connection.execute("pragma table_info(account_versions)").fetchall()}
        position_history_columns = {
            row[1] for row in connection.execute("pragma table_info(position_versions)").fetchall()
        }

    assert idempotency_columns == set(IdempotencyRow.__table__.columns.keys())
    assert history_columns == set(AccountVersionRow.__table__.columns.keys())
    assert position_history_columns == set(PositionVersionRow.__table__.columns.keys())


def test_packaged_migration_tree_is_the_only_authoring_environment() -> None:
    migration_directory = Path("src/vibe_portfolio/portfolio/migrations")

    assert (migration_directory / "env.py").is_file()
    assert (migration_directory / "script.py.mako").is_file()
    assert not Path("migrations/env.py").exists()
    assert not Path("migrations/script.py.mako").exists()
