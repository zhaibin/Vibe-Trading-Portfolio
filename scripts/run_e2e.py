"""Restart-safe production-build Playwright harness."""

import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SERVER = REPOSITORY / "scripts" / "run_e2e_server.py"
BASE_URL = "http://127.0.0.1:8875"
CAPABILITY_MARKER = ".portfolio-e2e-capability"
_ACTIVE_CHILDREN: set[subprocess.Popen[bytes]] = set()
SCENARIO_PRIVACY_TOKENS = (
    "人民币账户",
    "港币账户",
    "美元账户",
    "浦发银行",
    "腾讯控股",
    "Demo Corp",
    "600000.SH",
    "00700.HK",
    "DEMO.US",
    "eastmoney",
    "yahoo",
    "tencent",
    "1.600000",
    "0700.HK",
    "DEMO",
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
)


def _create_capability(root: Path) -> str:
    capability = secrets.token_urlsafe(32)
    marker = root / CAPABILITY_MARKER
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(f"{capability}\n{os.getpid()}\n")
    return capability


def _start_server(
    root: Path, database: Path, log_file: Path, capability: str
) -> tuple[subprocess.Popen[bytes], object]:
    try:
        with socket.create_connection(("127.0.0.1", 8875), timeout=0.1):
            raise RuntimeError("loopback port 8875 is already in use")
    except (ConnectionRefusedError, TimeoutError, OSError):
        pass
    environment = os.environ.copy()
    environment["PORTFOLIO_E2E"] = "1"
    environment["PORTFOLIO_E2E_CAPABILITY"] = capability
    log = log_file.open("ab")
    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVER),
            "--root",
            str(root),
            "--database",
            str(database),
            "--runner-pid",
            str(os.getpid()),
        ],
        cwd=REPOSITORY,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _ACTIVE_CHILDREN.add(process)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _ACTIVE_CHILDREN.discard(process)
            log.close()
            details = log_file.read_text(encoding="utf-8").replace(str(database), "<temporary-database>")
            raise RuntimeError(f"E2E server exited before becoming ready:\n{details[-2000:]}")
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/v1/system/status", timeout=0.25) as response:
                if response.status == 200:
                    time.sleep(0.1)
                    if process.poll() is not None:
                        _ACTIVE_CHILDREN.discard(process)
                        log.close()
                        raise RuntimeError("E2E server lost ownership of loopback port 8875")
                    return process, log
        except OSError:
            time.sleep(0.05)
    _stop_server(process, log)
    raise RuntimeError("E2E server readiness timed out")


def _stop_server(process: subprocess.Popen[bytes], log: object) -> None:
    _terminate_process(process)
    close = getattr(log, "close", None)
    if callable(close):
        close()


def _playwright(phase: str) -> None:
    process = subprocess.Popen(
        [
            "npm",
            "exec",
            "--prefix",
            "frontend",
            "--",
            "playwright",
            "test",
            "--config",
            "frontend/playwright.config.ts",
            "--grep",
            f"@{phase}",
        ],
        cwd=REPOSITORY,
        start_new_session=True,
    )
    _ACTIVE_CHILDREN.add(process)
    try:
        return_code = process.wait()
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, process.args)
    finally:
        _ACTIVE_CHILDREN.discard(process)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    finally:
        _ACTIVE_CHILDREN.discard(process)


def _handle_signal(signum: int, _frame: object) -> None:
    for process in tuple(_ACTIVE_CHILDREN):
        _terminate_process(process)
    raise SystemExit(128 + signum)


def _privacy_tokens(root: Path, database: Path) -> tuple[str, ...]:
    return (str(root), str(database), *SCENARIO_PRIVACY_TOKENS)


def _assert_private_logs(log_file: Path, root: Path, database: Path) -> None:
    if not log_file.exists():
        return
    text = log_file.read_text(encoding="utf-8")
    leaked = [value for value in _privacy_tokens(root, database) if value in text]
    if leaked:
        raise RuntimeError("E2E server log contained private scenario data")


def main() -> None:
    previous_handlers = {
        signum: signal.signal(signum, _handle_signal)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    safe_temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="portfolio-e2e-", dir=safe_temp_root) as directory:
            root = Path(directory).resolve(strict=True)
            database = root / "portfolio.db"
            log_file = root / "server.log"
            capability = _create_capability(root)
            process: subprocess.Popen[bytes] | None = None
            log: object | None = None
            try:
                process, log = _start_server(root, database, log_file, capability)
                _playwright("phase1")
                _stop_server(process, log)
                process = None
                log = None
                process, log = _start_server(root, database, log_file, capability)
                _playwright("phase2")
            finally:
                if process is not None and log is not None:
                    _stop_server(process, log)
                _assert_private_logs(log_file, root, database)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
