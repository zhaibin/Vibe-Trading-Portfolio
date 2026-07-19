import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vibe_portfolio.api.security import SecurityMiddleware
from vibe_portfolio.api.static import SpaStaticApp
from vibe_portfolio.compatibility import (
    AnalysisMode,
    CompatibilityDiscovery,
    CompatibilityReport,
    CompatibilityState,
    McpStatus,
)
from vibe_portfolio.config import Settings
from vibe_portfolio.market_data.router import build_market_data_router
from vibe_portfolio.market_data.service import (
    MarketDataService,
    build_live_provider_registry,
)
from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.router import build_portfolio_router
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.vibe.gateway import VibeGateway
from vibe_portfolio.vibe.mcp_probe import McpProbeResult, PortfolioMcpProbe
from vibe_portfolio.vibe.watcher import AttemptWatcher
from vibe_portfolio.web import web_dist_path


class DiscoveryPort(Protocol):
    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport: ...


class McpProbePort(Protocol):
    async def run(self) -> McpProbeResult: ...


T = TypeVar("T")
ServiceFactory = Callable[[], "AppServices"]


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
    database: Database | None = None
    portfolio: PortfolioService | None = None
    market_data: MarketDataService | None = None
    static_dir: Path | None = None
    settings: Settings | None = None


@dataclass(slots=True)
class _ServiceSlot:
    current: AppServices | None


class _ServiceProxy:
    def __init__(self, slot: _ServiceSlot, field: str) -> None:
        self._slot = slot
        self._field = field

    def __getattr__(self, name: str) -> Any:
        async def invoke(*args: object, **kwargs: object) -> object:
            active = self._slot.current
            service = None if active is None else getattr(active, self._field)
            if service is None:
                raise RuntimeError("application services are not running")
            return await getattr(service, name)(*args, **kwargs)

        return invoke


def build_services(settings: Settings) -> AppServices:
    gateway = VibeGateway(settings)
    database = Database(settings.database_path, settings.database_busy_timeout_ms)
    portfolio = PortfolioService(database)
    market_data = MarketDataService(
        database,
        build_live_provider_registry(settings),
        settings=settings,
    )
    watcher = AttemptWatcher(
        gateway,
        poll_interval_seconds=settings.vibe_poll_interval_seconds,
        timeout_seconds=settings.vibe_analysis_timeout_seconds,
    )
    return AppServices(
        discovery=CompatibilityDiscovery(gateway),
        mcp_probe=PortfolioMcpProbe(gateway, watcher),
        gateway=gateway,
        database=database,
        portfolio=portfolio,
        market_data=market_data,
        static_dir=web_dist_path(),
        settings=settings,
    )


async def _wait_for_terminal(task: asyncio.Task[T]) -> T:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException:
            break
    try:
        result = task.result()
    except BaseException:
        if cancellation is not None:
            raise cancellation from None
        raise
    if cancellation is not None:
        raise cancellation
    return result


async def _run_to_terminal(awaitable: Coroutine[Any, Any, T]) -> T:
    return await _wait_for_terminal(asyncio.create_task(awaitable))


async def _close_services(services: AppServices) -> list[BaseException]:
    errors: list[BaseException] = []
    closers: list[Callable[[], Coroutine[Any, Any, None]]] = []
    if services.market_data is not None:
        closers.append(services.market_data.aclose)
    if services.database is not None:
        closers.append(services.database.close)
    if services.gateway is not None:
        closers.append(services.gateway.close)
    for close in closers:
        try:
            await _run_to_terminal(close())
        except BaseException as error:
            errors.append(error)
    return errors


def _raise_lifecycle_errors(errors: list[BaseException]) -> None:
    if not errors:
        return
    for error in errors:
        if isinstance(error, asyncio.CancelledError):
            raise error from None
    if len(errors) == 1:
        raise errors[0]
    raise BaseExceptionGroup("application lifecycle failed", errors)


def create_app(
    services: AppServices | None = None,
    *,
    service_factory: ServiceFactory | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    if services is not None and service_factory is not None:
        raise ValueError("provide services or service_factory, not both")
    runtime_settings = settings or (None if services is None else services.settings) or Settings()
    fixed_services = services
    factory = service_factory or (lambda: build_services(runtime_settings))
    service_slot = _ServiceSlot(fixed_services)
    factory_mode = services is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = factory() if factory_mode else fixed_services
        assert active is not None
        service_slot.current = active
        app.state.services = active
        app.state.mcp_status = McpStatus.NOT_CHECKED
        app.state._mcp_probe_lock = asyncio.Lock()
        errors: list[BaseException] = []
        try:
            if active.database is not None:
                await active.database.start()
            if active.market_data is not None and active.database is not None:
                await active.market_data.startup()
            yield
        except BaseException as error:
            errors.append(error)
        errors.extend(await _close_services(active))
        if factory_mode:
            service_slot.current = None
            app.state.services = None
        _raise_lifecycle_errors(errors)

    app = FastAPI(
        title="Vibe-Trading Portfolio",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[runtime_settings.api_host, "localhost"],
        www_redirect=False,
    )
    app.add_middleware(SecurityMiddleware, settings=runtime_settings)
    app.state.services = fixed_services
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

    portfolio: PortfolioService | None
    market_data: MarketDataService | None
    static_dir: Path | None
    if factory_mode:
        portfolio = cast(PortfolioService, _ServiceProxy(service_slot, "portfolio"))
        market_data = cast(MarketDataService, _ServiceProxy(service_slot, "market_data"))
        static_dir = web_dist_path()
    else:
        assert fixed_services is not None
        portfolio = fixed_services.portfolio
        market_data = fixed_services.market_data
        static_dir = fixed_services.static_dir
    if portfolio is not None:
        app.include_router(build_portfolio_router(portfolio))
    if market_data is not None:
        app.include_router(build_market_data_router(market_data))
    if static_dir is not None:
        app.mount("/", SpaStaticApp(static_dir), name="portfolio-web")

    return app
