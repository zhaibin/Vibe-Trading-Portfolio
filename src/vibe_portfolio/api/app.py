from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI, Request

from vibe_portfolio.compatibility import CompatibilityDiscovery, CompatibilityReport, McpStatus
from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.gateway import VibeGateway
from vibe_portfolio.vibe.mcp_probe import McpProbeResult, PortfolioMcpProbe
from vibe_portfolio.vibe.watcher import AttemptWatcher


class DiscoveryPort(Protocol):
    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport: ...


class McpProbePort(Protocol):
    async def run(self) -> McpProbeResult: ...


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

    app = FastAPI(title="Vibe-Trading Portfolio", version="0.1.0", lifespan=lifespan)
    app.state.services = configured
    app.state.mcp_status = McpStatus.NOT_CHECKED

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
        mcp_status: McpStatus = request.app.state.mcp_status
        return await active_services.discovery.discover(mcp_status)

    @app.post("/api/v1/system/compatibility/mcp-probe")
    async def probe_mcp(request: Request) -> dict[str, object]:
        active_services: AppServices = request.app.state.services
        result = await active_services.mcp_probe.run()
        request.app.state.mcp_status = result.status
        report = await active_services.discovery.discover(result.status)
        return {"probe": result, "compatibility": report}

    return app
