"""Run the production SPA against an explicit disposable database and fake providers."""

import argparse
import os
import stat
import tempfile
from pathlib import Path
from typing import cast

import uvicorn
from e2e_fakes import build_e2e_provider_registry

from vibe_portfolio.api.app import AppServices, create_app
from vibe_portfolio.compatibility import (
    AnalysisMode,
    CompatibilityReport,
    CompatibilityState,
    McpStatus,
)
from vibe_portfolio.config import Settings
from vibe_portfolio.market_data.service import MarketDataService
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.vibe.mcp_probe import McpProbeResult
from vibe_portfolio.web import web_dist_path


class _OfflineDiscovery:
    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
        return CompatibilityReport(
            state=CompatibilityState.DEGRADED,
            analysis_mode=AnalysisMode.DISABLED,
            contract_compatible=False,
            deep_analysis_enabled=False,
            mcp_status=mcp_status,
            reasons=["e2e_fake_runtime"],
        )


class _OfflineMcpProbe:
    async def run(self) -> McpProbeResult:
        return McpProbeResult(
            status=McpStatus.FAILED,
            session_id="e2e",
            attempt_id="e2e",
            observed_tools=[],
            reason="e2e_fake_runtime",
        )


CAPABILITY_MARKER = ".portfolio-e2e-capability"


def validate_e2e_database(
    root: Path, database: Path, capability: str, runner_pid: int
) -> Path:
    try:
        root_metadata = root.lstat()
        resolved_root = root.resolve(strict=True)
        marker = root / CAPABILITY_MARKER
        marker_metadata = marker.lstat()
        marker_text = marker.read_text(encoding="utf-8")
        resolved_database = database.resolve(strict=False)
    except OSError as error:
        raise SystemExit("invalid E2E temporary-root capability") from error
    valid = (
        root.is_absolute()
        and resolved_root == root
        and resolved_root.parent == Path(tempfile.gettempdir()).resolve()
        and root.name.startswith("portfolio-e2e-")
        and stat.S_ISDIR(root_metadata.st_mode)
        and stat.S_IMODE(root_metadata.st_mode) == 0o700
        and root_metadata.st_uid == os.getuid()
        and stat.S_ISREG(marker_metadata.st_mode)
        and stat.S_IMODE(marker_metadata.st_mode) == 0o600
        and marker_metadata.st_uid == os.getuid()
        and marker_metadata.st_nlink == 1
        and bool(capability)
        and marker_text == f"{capability}\n{runner_pid}\n"
        and runner_pid == os.getppid()
        and resolved_database.parent == resolved_root
        and resolved_database.name == "portfolio.db"
        and not database.is_symlink()
    )
    if not valid:
        raise SystemExit("invalid E2E temporary-root capability")
    return resolved_database


def _database_argument() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--runner-pid", type=int, required=True)
    arguments = parser.parse_args()
    root = cast(Path, arguments.root)
    path = cast(Path, arguments.database)
    runner_pid = cast(int, arguments.runner_pid)
    if os.environ.get("PORTFOLIO_E2E") != "1":
        raise SystemExit("PORTFOLIO_E2E=1 is required")
    capability = os.environ.get("PORTFOLIO_E2E_CAPABILITY", "")
    return validate_e2e_database(root, path, capability, runner_pid)


def main() -> None:
    database_path = _database_argument()
    settings = Settings(api_port=8875, database_path=database_path)
    database = Database(database_path, settings.database_busy_timeout_ms)
    services = AppServices(
        discovery=_OfflineDiscovery(),
        mcp_probe=_OfflineMcpProbe(),
        database=database,
        portfolio=PortfolioService(database),
        market_data=MarketDataService(
            database,
            build_e2e_provider_registry(),
            settings=settings,
        ),
        static_dir=web_dist_path(),
        settings=settings,
    )
    uvicorn.run(
        create_app(services=services),
        host="127.0.0.1",
        port=8875,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
