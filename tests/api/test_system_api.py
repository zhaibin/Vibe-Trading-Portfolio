import asyncio

import httpx
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


class FailingAfterAvailabilityDiscovery(FakeDiscovery):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
        if self.fail:
            raise RuntimeError("upstream_api_key=must-not-leak")
        return await super().discover(mcp_status)


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


class AvailableThenFailingProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.failure_entered = asyncio.Event()
        self.release_failure = asyncio.Event()

    async def run(self) -> McpProbeResult:
        self.calls += 1
        if self.calls == 1:
            return McpProbeResult(McpStatus.AVAILABLE, "session-1", "attempt-1", ["portfolio-tool"])
        self.failure_entered.set()
        await self.release_failure.wait()
        raise RuntimeError("broker_token=must-not-leak")


class OrderedProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.first_entered = asyncio.Event()
        self.release_first = asyncio.Event()

    async def run(self) -> McpProbeResult:
        self.calls += 1
        call_number = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if call_number == 1:
                self.first_entered.set()
                await self.release_first.wait()
                return McpProbeResult(McpStatus.AVAILABLE, "session-1", "attempt-1", ["portfolio-tool"])
            return McpProbeResult(McpStatus.FAILED, "session-2", "attempt-2", [], "second_probe_failed")
        finally:
            self.active -= 1


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


async def test_probe_failure_immediately_invalidates_cached_availability_and_is_sanitized() -> None:
    probe = AvailableThenFailingProbe()
    app = create_app(AppServices(discovery=FakeDiscovery(), mcp_probe=probe))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        first = await client.post("/api/v1/system/compatibility/mcp-probe")
        failed_task = asyncio.create_task(client.post("/api/v1/system/compatibility/mcp-probe"))
        await probe.failure_entered.wait()
        while_running = await client.get("/api/v1/system/status")
        probe.release_failure.set()
        failed = await failed_task
        compatibility = await client.get("/api/v1/system/compatibility")

    assert first.status_code == 200
    assert while_running.json()["mcp_status"] == "failed"
    assert failed.status_code == 502
    assert failed.json()["error"] == {
        "code": "MCP_PROBE_FAILED",
        "message": "Portfolio MCP compatibility probe failed",
    }
    assert failed.json()["probe"] == {"status": "failed", "reason": "probe_execution_failed"}
    assert "must-not-leak" not in failed.text
    assert compatibility.json()["state"] == "degraded"
    assert compatibility.json()["mcp_status"] == "failed"


async def test_concurrent_probe_requests_are_serialized_and_preserve_request_order() -> None:
    probe = OrderedProbe()
    app = create_app(AppServices(discovery=FakeDiscovery(), mcp_probe=probe))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        first_task = asyncio.create_task(client.post("/api/v1/system/compatibility/mcp-probe"))
        await probe.first_entered.wait()
        second_task = asyncio.create_task(client.post("/api/v1/system/compatibility/mcp-probe"))
        await asyncio.sleep(0.01)
        calls_before_first_completed = probe.calls
        probe.release_first.set()
        first, second = await asyncio.gather(first_task, second_task)
        status = await client.get("/api/v1/system/status")

    assert calls_before_first_completed == 1
    assert probe.max_active == 1
    assert first.json()["probe"]["status"] == "available"
    assert second.json()["probe"]["status"] == "failed"
    assert status.json()["mcp_status"] == "failed"


def test_get_discovery_failure_invalidates_cached_availability_without_running_probe() -> None:
    discovery = FailingAfterAvailabilityDiscovery()
    probe = FakeProbe()
    app = create_app(AppServices(discovery=discovery, mcp_probe=probe))

    with TestClient(app, raise_server_exceptions=False) as client:
        available = client.post("/api/v1/system/compatibility/mcp-probe")
        discovery.fail = True
        failed = client.get("/api/v1/system/compatibility")
        status = client.get("/api/v1/system/status")
        repeated = client.get("/api/v1/system/compatibility")

    assert available.status_code == 200
    assert available.json()["compatibility"]["analysis_mode"] == "full_mcp"
    assert failed.status_code == 200
    assert failed.json()["state"] == "degraded"
    assert failed.json()["analysis_mode"] == "disabled"
    assert failed.json()["contract_compatible"] is False
    assert failed.json()["deep_analysis_enabled"] is False
    assert failed.json()["mcp_status"] == "failed"
    assert failed.json()["reasons"] == ["compatibility_discovery_failed"]
    assert "must-not-leak" not in failed.text
    assert status.json()["mcp_status"] == "failed"
    assert repeated.json()["analysis_mode"] != "full_mcp"
    assert repeated.json()["mcp_status"] == "failed"
    assert probe.calls == 1
