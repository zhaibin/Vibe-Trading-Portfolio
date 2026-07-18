import argparse
import asyncio

from vibe_portfolio.compatibility import (
    AnalysisMode,
    CompatibilityDiscovery,
    CompatibilityReport,
    CompatibilityState,
    McpStatus,
)
from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.gateway import VibeGateway


def _failed_report() -> CompatibilityReport:
    return CompatibilityReport(
        state=CompatibilityState.DEGRADED,
        analysis_mode=AnalysisMode.DISABLED,
        contract_compatible=False,
        deep_analysis_enabled=False,
        mcp_status=McpStatus.NOT_CHECKED,
        reasons=["compatibility_check_failed"],
    )


async def _check(contract_only: bool) -> int:
    try:
        gateway = VibeGateway(Settings())
        try:
            report = await CompatibilityDiscovery(gateway).discover(McpStatus.NOT_CHECKED)
        finally:
            await gateway.close()
    except Exception:
        report = _failed_report()

    print(report.model_dump_json(indent=2))
    if contract_only:
        return 0 if report.contract_compatible else 2
    return 0 if report.deep_analysis_enabled else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Vibe-Trading compatibility")
    parser.add_argument(
        "--contract-only",
        action="store_true",
        help="Require version and routes but allow provider readiness and MCP verification to remain degraded",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_check(args.contract_only)))


if __name__ == "__main__":
    main()
