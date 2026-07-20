import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

import run_e2e  # noqa: E402
from run_e2e import SCENARIO_PRIVACY_TOKENS, _assert_private_logs, _privacy_tokens  # noqa: E402
from run_e2e_server import validate_e2e_database  # noqa: E402


def test_database_guard_requires_live_capability_direct_child_and_no_symlinks() -> None:
    safe_temp = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="portfolio-e2e-owned-", dir=safe_temp) as directory:
        root = Path(directory).resolve()
        marker = root / ".portfolio-e2e-capability"
        marker.write_text(f"capability\n{os.getppid()}\n", encoding="utf-8")
        marker.chmod(0o600)
        database = root / "portfolio.db"

        assert validate_e2e_database(root, database, "capability", os.getppid()) == database
        with pytest.raises(SystemExit):
            validate_e2e_database(root, database, "", os.getppid())
        with pytest.raises(SystemExit):
            validate_e2e_database(root, database, "wrong", os.getppid())
        nested = root / "nested"
        nested.mkdir()
        with pytest.raises(SystemExit):
            validate_e2e_database(root, nested / "portfolio.db", "capability", os.getppid())
        alias = safe_temp / f"portfolio-e2e-alias-{os.getpid()}"
        alias.symlink_to(root, target_is_directory=True)
        try:
            with pytest.raises(SystemExit):
                validate_e2e_database(alias, alias / "portfolio.db", "capability", os.getppid())
        finally:
            alias.unlink()


def test_database_guard_rejects_crafted_prefix_outside_system_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed-temp"
    allowed.mkdir()
    crafted = tmp_path / "persistent" / "portfolio-e2e-crafted"
    crafted.mkdir(parents=True, mode=0o700)
    marker = crafted / ".portfolio-e2e-capability"
    marker.write_text(f"capability\n{os.getppid()}\n", encoding="utf-8")
    marker.chmod(0o600)
    monkeypatch.setattr("run_e2e_server.tempfile.gettempdir", lambda: str(allowed))

    with pytest.raises(SystemExit):
        validate_e2e_database(crafted, crafted / "portfolio.db", "capability", os.getppid())


def test_privacy_tokens_cover_scenario_identifiers_and_paths(tmp_path: Path) -> None:
    database = tmp_path / "portfolio.db"
    tokens = set(_privacy_tokens(tmp_path, database))
    assert {str(tmp_path), str(database)} <= tokens
    assert {"人民币账户", "港币账户", "美元账户"} <= tokens
    assert {"浦发银行", "腾讯控股", "Demo Corp"} <= tokens
    assert {"600000.SH", "00700.HK", "DEMO.US", "eastmoney", "yahoo", "tencent"} <= tokens
    assert {"1.600000", "0700.HK", "DEMO"} <= tokens
    assert {
        "91001.11",
        "92002.22",
        "93003.33",
        "17.12345678",
        "8101.01",
        "27.23456789",
        "8202.02",
        "37.34567891",
        "8303.03",
        "18.12345678",
        "91011.11",
        "91022.22",
    } <= tokens
    assert {"8", "3", "10"}.isdisjoint(tokens)


def test_privacy_scan_accepts_benign_server_log(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text("server lifecycle completed safely\n", encoding="utf-8")
    _assert_private_logs(log, tmp_path, tmp_path / "portfolio.db")


@pytest.mark.parametrize("token", ("<root>", "<database>", *SCENARIO_PRIVACY_TOKENS))
def test_privacy_scan_rejects_each_sensitive_token(tmp_path: Path, token: str) -> None:
    database = tmp_path / "portfolio.db"
    rendered = str(tmp_path) if token == "<root>" else str(database) if token == "<database>" else token
    log = tmp_path / "server.log"
    log.write_text(f"unexpected: {rendered}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="private scenario data"):
        _assert_private_logs(log, tmp_path, database)


def test_signal_handler_terminates_every_active_process(monkeypatch: pytest.MonkeyPatch) -> None:
    first = object()
    second = object()
    terminated: list[object] = []
    monkeypatch.setattr(run_e2e, "_ACTIVE_CHILDREN", {first, second})
    monkeypatch.setattr(run_e2e, "_terminate_process", terminated.append)

    with pytest.raises(SystemExit) as stopped:
        run_e2e._handle_signal(15, None)

    assert stopped.value.code == 143
    assert set(terminated) == {first, second}
