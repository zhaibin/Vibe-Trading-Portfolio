from typing import Any

import pytest

from vibe_portfolio.compatibility import (
    REQUIRED_ENDPOINTS,
    AnalysisMode,
    CompatibilityDiscovery,
    CompatibilityState,
    McpStatus,
)
from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.models import ApiInfo, ProbeResult


class FakeGateway:
    def __init__(
        self,
        *,
        version: str = "0.1.11",
        paths: dict[str, Any] | None = None,
        ready: bool = True,
        offline: bool = False,
    ) -> None:
        self.version = version
        self.paths = paths if paths is not None else required_paths()
        self.is_ready = ready
        self.offline = offline

    async def api_info(self) -> ApiInfo:
        if self.offline:
            raise GatewayError(GatewayErrorCode.VIBE_UNAVAILABLE, "offline")
        return ApiInfo(service="Vibe-Trading API", version=self.version, docs="/docs", health="/health")

    async def openapi(self) -> dict[str, Any]:
        return {"openapi": "3.1.0", "paths": self.paths}

    async def live(self) -> ProbeResult:
        return ProbeResult(ok=True, status_code=200)

    async def ready(self) -> ProbeResult:
        return ProbeResult(
            ok=self.is_ready,
            status_code=200 if self.is_ready else 503,
            detail=None if self.is_ready else "LLM not configured",
        )


def required_paths(*, exclude: str | None = None) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for _, method, path in REQUIRED_ENDPOINTS:
        if path == exclude:
            continue
        paths.setdefault(path, {})[method.lower()] = {}
    return paths


@pytest.mark.parametrize("mcp_status", [McpStatus.NOT_CHECKED, McpStatus.MISSING])
async def test_supported_contract_without_mcp_is_bounded_and_degraded(mcp_status: McpStatus) -> None:
    report = await CompatibilityDiscovery(FakeGateway()).discover(mcp_status)

    assert report.state is CompatibilityState.DEGRADED
    assert report.analysis_mode is AnalysisMode.BOUNDED_CONTEXT
    assert report.contract_compatible is True
    assert report.deep_analysis_enabled is True


async def test_supported_ready_contract_with_mcp_is_compatible() -> None:
    report = await CompatibilityDiscovery(FakeGateway()).discover(McpStatus.AVAILABLE)

    assert report.state is CompatibilityState.COMPATIBLE
    assert report.analysis_mode is AnalysisMode.FULL_MCP
    assert report.missing_capabilities == []


async def test_unknown_version_fails_closed() -> None:
    report = await CompatibilityDiscovery(FakeGateway(version="0.2.0")).discover(McpStatus.AVAILABLE)

    assert report.state is CompatibilityState.UNSUPPORTED
    assert report.analysis_mode is AnalysisMode.DISABLED
    assert report.contract_compatible is False
    assert "version_out_of_range" in report.reasons


async def test_missing_required_route_fails_closed() -> None:
    paths = required_paths(exclude="/sessions/{session_id}/cancel")
    report = await CompatibilityDiscovery(FakeGateway(paths=paths)).discover(McpStatus.AVAILABLE)

    assert report.state is CompatibilityState.UNSUPPORTED
    assert "sessions.cancel" in report.missing_capabilities


async def test_offline_and_not_ready_keep_local_product_available() -> None:
    offline = await CompatibilityDiscovery(FakeGateway(offline=True)).discover(McpStatus.NOT_CHECKED)
    not_ready = await CompatibilityDiscovery(FakeGateway(ready=False)).discover(McpStatus.AVAILABLE)

    assert offline.state is CompatibilityState.DEGRADED
    assert offline.analysis_mode is AnalysisMode.DISABLED
    assert offline.contract_compatible is False
    assert not_ready.state is CompatibilityState.DEGRADED
    assert not_ready.analysis_mode is AnalysisMode.DISABLED
