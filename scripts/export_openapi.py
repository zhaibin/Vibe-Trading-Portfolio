"""Export the complete Sidecar API contract without runtime I/O."""

import json
from pathlib import Path
from typing import cast

from fastapi import FastAPI

from vibe_portfolio.api.app import AppServices, create_app
from vibe_portfolio.compatibility import AnalysisMode, CompatibilityReport, CompatibilityState, McpStatus
from vibe_portfolio.market_data.service import MarketDataService
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.vibe.mcp_probe import McpProbeResult

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "frontend/openapi.json"


class _ContractDiscovery:
    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
        return CompatibilityReport(
            state=CompatibilityState.COMPATIBLE,
            analysis_mode=AnalysisMode.BOUNDED_CONTEXT,
            contract_compatible=True,
            deep_analysis_enabled=False,
            vibe_version="0.1.11",
            mcp_status=mcp_status,
        )


class _ContractProbe:
    async def run(self) -> McpProbeResult:
        return McpProbeResult(McpStatus.FAILED, None, None, [], "contract-only")


def _contract_app() -> FastAPI:
    return create_app(
        AppServices(
            discovery=_ContractDiscovery(),
            mcp_probe=_ContractProbe(),
            portfolio=cast(PortfolioService, object()),
            market_data=cast(MarketDataService, object()),
        )
    )


def export_openapi(output: Path = DEFAULT_OUTPUT) -> None:
    """Write a stable complete OpenAPI document to *output*."""
    document = _contract_app().openapi()
    serialized = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{serialized}\n", encoding="utf-8")


def main() -> None:
    export_openapi()


if __name__ == "__main__":
    main()
