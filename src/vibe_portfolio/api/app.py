import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vibe_portfolio.compatibility import (
    AnalysisMode,
    CompatibilityDiscovery,
    CompatibilityReport,
    CompatibilityState,
    McpStatus,
)
from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.gateway import VibeGateway
from vibe_portfolio.vibe.mcp_probe import McpProbeResult, PortfolioMcpProbe
from vibe_portfolio.vibe.watcher import AttemptWatcher


class DiscoveryPort(Protocol):
    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport: ...


class McpProbePort(Protocol):
    async def run(self) -> McpProbeResult: ...


class ProbeError(BaseModel):
    code: str = "MCP_PROBE_FAILED"
    message: str = "Portfolio MCP compatibility probe failed"


class FailedProbe(BaseModel):
    status: McpStatus = McpStatus.FAILED
    reason: str = "probe_execution_failed"


class ProbeFailureResponse(BaseModel):
    error: ProbeError
    probe: FailedProbe
    compatibility: CompatibilityReport


def _failed_compatibility_report(reason: str) -> CompatibilityReport:
    return CompatibilityReport(
        state=CompatibilityState.DEGRADED,
        analysis_mode=AnalysisMode.DISABLED,
        contract_compatible=False,
        deep_analysis_enabled=False,
        mcp_status=McpStatus.FAILED,
        reasons=[reason],
    )


@dataclass(slots=True)
class AppServices:
    discovery: DiscoveryPort
    mcp_probe: McpProbePort
    gateway: VibeGateway | None = None


def build_services(settings: Settings) -> AppServices:
    gateway = VibeGateway(settings)
    watcher = AttemptWatcher(
        gateway,
        poll_interval_seconds=settings.vibe_poll_interval_seconds,
        timeout_seconds=settings.vibe_analysis_timeout_seconds,
    )
    return AppServices(
        discovery=CompatibilityDiscovery(gateway),
        mcp_probe=PortfolioMcpProbe(gateway, watcher),
        gateway=gateway,
    )


def create_app(services: AppServices | None = None) -> FastAPI:
    configured = services or build_services(Settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if configured.gateway is not None:
                await configured.gateway.close()

    app = FastAPI(
        title="Vibe-Trading Portfolio",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.services = configured
    app.state.mcp_status = McpStatus.NOT_CHECKED
    app.state._mcp_probe_lock = asyncio.Lock()

    @app.get("/api/v1/system/status")
    async def system_status(request: Request) -> dict[str, str]:
        mcp_status: McpStatus = request.app.state.mcp_status
        return {
            "status": "ok",
            "service": "Vibe-Trading Portfolio",
            "mcp_status": mcp_status.value,
        }

    @app.get("/api/v1/system/compatibility", response_model=CompatibilityReport)
    async def compatibility(request: Request) -> CompatibilityReport:
        active_services: AppServices = request.app.state.services
        lock: asyncio.Lock = request.app.state._mcp_probe_lock
        async with lock:
            mcp_status: McpStatus = request.app.state.mcp_status
            try:
                return await active_services.discovery.discover(mcp_status)
            except Exception:
                request.app.state.mcp_status = McpStatus.FAILED
                return _failed_compatibility_report("compatibility_discovery_failed")

    @app.post(
        "/api/v1/system/compatibility/mcp-probe",
        response_model=None,
        responses={502: {"model": ProbeFailureResponse}},
    )
    async def probe_mcp(request: Request) -> dict[str, object] | JSONResponse:
        active_services: AppServices = request.app.state.services
        lock: asyncio.Lock = request.app.state._mcp_probe_lock
        async with lock:
            request.app.state.mcp_status = McpStatus.FAILED
            try:
                result = await active_services.mcp_probe.run()
                report = await active_services.discovery.discover(result.status)
            except Exception:
                request.app.state.mcp_status = McpStatus.FAILED
                try:
                    report = await active_services.discovery.discover(McpStatus.FAILED)
                except Exception:
                    report = _failed_compatibility_report("mcp_probe_failed")
                failure = ProbeFailureResponse(
                    error=ProbeError(),
                    probe=FailedProbe(),
                    compatibility=report,
                )
                return JSONResponse(status_code=502, content=failure.model_dump(mode="json"))

            request.app.state.mcp_status = result.status
            return {"probe": result, "compatibility": report}

    return app
