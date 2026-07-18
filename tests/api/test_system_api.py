from fastapi.testclient import TestClient

from vibe_portfolio.api.app import AppServices, create_app
from vibe_portfolio.compatibility import AnalysisMode, CompatibilityReport, CompatibilityState, McpStatus
from vibe_portfolio.vibe.mcp_probe import McpProbeResult


class FakeDiscovery:
    def __init__(self) -> None:
        self.statuses: list[McpStatus] = []

    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
        self.statuses.append(mcp_status)
        state = CompatibilityState.COMPATIBLE if mcp_status is McpStatus.AVAILABLE else CompatibilityState.DEGRADED
        mode = AnalysisMode.FULL_MCP if mcp_status is McpStatus.AVAILABLE else AnalysisMode.BOUNDED_CONTEXT
        return CompatibilityReport(
            state=state,
            analysis_mode=mode,
            contract_compatible=True,
            deep_analysis_enabled=True,
            vibe_version="0.1.11",
            mcp_status=mcp_status,
        )


class FakeProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self) -> McpProbeResult:
        self.calls += 1
        return McpProbeResult(
            McpStatus.AVAILABLE,
            "session-1",
            "attempt-1",
            ["mcp_portfolio_portfolio_get_capabilities"],
        )


def test_get_compatibility_is_read_only_and_does_not_run_mcp_probe() -> None:
    discovery = FakeDiscovery()
    probe = FakeProbe()
    app = create_app(AppServices(discovery=discovery, mcp_probe=probe))

    with TestClient(app) as client:
        response = client.get("/api/v1/system/compatibility")

    assert response.status_code == 200
    assert response.json()["state"] == "degraded"
    assert response.json()["mcp_status"] == "not_checked"
    assert discovery.statuses == [McpStatus.NOT_CHECKED]
    assert probe.calls == 0


def test_post_probe_is_explicit_and_updates_compatibility() -> None:
    discovery = FakeDiscovery()
    probe = FakeProbe()
    app = create_app(AppServices(discovery=discovery, mcp_probe=probe))

    with TestClient(app) as client:
        before = client.get("/api/v1/system/status")
        result = client.post("/api/v1/system/compatibility/mcp-probe")
        compatibility = client.get("/api/v1/system/compatibility")
        status = client.get("/api/v1/system/status")

    assert before.json()["mcp_status"] == "not_checked"
    assert result.status_code == 200
    assert result.json()["probe"]["status"] == "available"
    assert result.json()["compatibility"]["state"] == "compatible"
    assert compatibility.json()["mcp_status"] == "available"
    assert status.json() == {
        "status": "ok",
        "service": "Vibe-Trading Portfolio",
        "mcp_status": "available",
    }
    assert probe.calls == 1


def test_system_api_exposes_only_diagnostics_and_explicit_read_only_probe() -> None:
    app = create_app(AppServices(discovery=FakeDiscovery(), mcp_probe=FakeProbe()))

    operations = {
        (path, method)
        for path, item in app.openapi()["paths"].items()
        for method in item
    }

    assert operations == {
        ("/api/v1/system/status", "get"),
        ("/api/v1/system/compatibility", "get"),
        ("/api/v1/system/compatibility/mcp-probe", "post"),
    }
