import asyncio
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vibe_portfolio.api.app import AppServices, create_app
from vibe_portfolio.compatibility import AnalysisMode, CompatibilityReport, CompatibilityState, McpStatus
from vibe_portfolio.market_data.service import MarketDataService
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.vibe.mcp_probe import McpProbeResult

BASE_URL = "http://127.0.0.1:8765"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


class FakeDiscovery:
    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
        return CompatibilityReport(
            state=CompatibilityState.COMPATIBLE,
            analysis_mode=AnalysisMode.BOUNDED_CONTEXT,
            contract_compatible=True,
            deep_analysis_enabled=False,
            vibe_version="0.1.11",
            mcp_status=mcp_status,
        )


class FakeProbe:
    async def run(self) -> McpProbeResult:
        return McpProbeResult(McpStatus.AVAILABLE, "session-1", "attempt-1", ["portfolio-tool"])


class NoopMarketData:
    async def startup(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def full_app() -> FastAPI:
    return create_app(
        AppServices(
            discovery=FakeDiscovery(),
            mcp_probe=FakeProbe(),
            portfolio=cast(PortfolioService, object()),
            market_data=cast(MarketDataService, NoopMarketData()),
        )
    )


def test_host_must_be_an_expected_loopback_origin() -> None:
    app = full_app()
    with TestClient(app, base_url=BASE_URL) as client:
        allowed = client.get("/api/v1/system/status")
        localhost = client.get("/api/v1/system/status", headers={"Host": "localhost:8765"})
        attacker = client.get("/api/v1/system/status", headers={"Host": "attacker.example"})
        wrong_port = client.get("/api/v1/system/status", headers={"Host": "127.0.0.1:9999"})

    assert allowed.status_code == localhost.status_code == 200
    assert attacker.status_code == wrong_port.status_code == 400


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_writes_require_an_exact_allowed_origin(method: str) -> None:
    app = full_app()
    path = "/api/v1/system/compatibility/mcp-probe" if method == "POST" else f"/api/v1/accounts/{UUID(int=1)}"
    with TestClient(app, base_url=BASE_URL) as client:
        missing = client.request(method, path, json={} if method == "PATCH" else None)
        attacker = client.request(
            method,
            path,
            json={} if method == "PATCH" else None,
            headers={"Origin": "http://attacker.example"},
        )

    assert missing.status_code == attacker.status_code == 403
    assert missing.json() == attacker.json() == {"error": {"code": "ORIGIN_FORBIDDEN"}}


def test_write_fetch_metadata_must_be_same_origin_when_present() -> None:
    app = full_app()
    with TestClient(app, base_url=BASE_URL) as client:
        cross_site = client.post(
            "/api/v1/system/compatibility/mcp-probe",
            headers={"Origin": BASE_URL, "Sec-Fetch-Site": "cross-site"},
        )
        same_origin = client.post(
            "/api/v1/system/compatibility/mcp-probe",
            headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        )
        omitted = client.post(
            "/api/v1/system/compatibility/mcp-probe",
            headers={"Origin": "http://localhost:8765", "Host": "localhost:8765"},
        )

    assert cross_site.status_code == 403
    assert cross_site.json() == {"error": {"code": "CROSS_SITE_FORBIDDEN"}}
    assert same_origin.status_code == omitted.status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/accounts"),
        ("PATCH", f"/api/v1/accounts/{UUID(int=1)}"),
    ],
)
def test_portfolio_writes_require_json(method: str, path: str) -> None:
    app = full_app()
    with TestClient(app, base_url=BASE_URL) as client:
        rejected = client.request(method, path, content="{}", headers={"Origin": BASE_URL})
        admitted = client.request(method, path, json={}, headers={"Origin": BASE_URL})

    assert rejected.status_code == 415
    assert rejected.json() == {"error": {"code": "JSON_REQUIRED"}}
    assert admitted.status_code == 422


def test_zero_body_probe_remains_allowed_without_json_content_type() -> None:
    app = full_app()
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/v1/system/compatibility/mcp-probe",
            headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        )

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({}, b"x"),
        ({"Content-Type": "text/plain"}, b"{}"),
    ],
)
def test_nonempty_probe_body_requires_json_content_type(
    headers: dict[str, str], body: bytes
) -> None:
    app = full_app()
    request_headers = {"Origin": BASE_URL, **headers}
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/v1/system/compatibility/mcp-probe",
            headers=request_headers,
            content=body,
        )

    assert response.status_code == 415
    assert response.json() == {"error": {"code": "JSON_REQUIRED"}}


def test_nonempty_probe_body_allows_application_json() -> None:
    app = full_app()
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/v1/system/compatibility/mcp-probe",
            headers={"Origin": BASE_URL, "Content-Type": "application/json"},
            content=b"{}",
        )

    assert response.status_code == 200


async def test_chunked_nonempty_probe_body_requires_json_content_type() -> None:
    app = full_app()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"{"
        yield b"}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=BASE_URL
    ) as client:
        response = await client.post(
            "/api/v1/system/compatibility/mcp-probe",
            content=chunks(),
            headers={"Origin": BASE_URL},
        )

    assert response.status_code == 415
    assert response.json() == {"error": {"code": "JSON_REQUIRED"}}


async def test_chunked_request_body_is_bounded_before_routing() -> None:
    app = full_app()

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(65):
            yield b"x" * 1_000

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=BASE_URL
    ) as client:
        response = await client.post(
            "/api/v1/system/compatibility/mcp-probe",
            content=chunks(),
            headers={"Origin": BASE_URL, "Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json() == {"error": {"code": "REQUEST_TOO_LARGE"}}


def test_security_headers_and_api_no_store_apply_without_cors() -> None:
    app = full_app()
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.get("/api/v1/system/status")

    assert response.headers["Content-Security-Policy"] == CSP
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    assert response.headers["Cache-Control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers


def assert_hardened_error(response: httpx.Response, *, cache_control: str = "no-store") -> None:
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}
    assert "must-not-leak" not in response.text
    assert response.headers["Content-Security-Policy"] == CSP
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    assert response.headers["Cache-Control"] == cache_control


def test_unhandled_api_exception_is_sanitized_inside_response_policy() -> None:
    app = create_app(AppServices(discovery=FakeDiscovery(), mcp_probe=FakeProbe()))

    @app.get("/api/v1/failure")
    async def failure() -> None:
        raise RuntimeError("broker_token=must-not-leak")

    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/failure")

    assert_hardened_error(response)


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "BREW"])
def test_unknown_unsafe_methods_require_origin_fetch_metadata_and_body_bound(method: str) -> None:
    app = create_app(AppServices(discovery=FakeDiscovery(), mcp_probe=FakeProbe()))
    with TestClient(app, base_url=BASE_URL) as client:
        missing_origin = client.request(method, "/api/v1/system/status")
        cross_site = client.request(
            method,
            "/api/v1/system/status",
            headers={"Origin": BASE_URL, "Sec-Fetch-Site": "cross-site"},
        )
        admitted = client.request(
            method,
            "/api/v1/system/status",
            headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        )
        wrong_type = client.request(
            method,
            "/api/v1/system/status",
            content=b"{}",
            headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        )
        oversized = client.request(
            method,
            "/api/v1/system/status",
            content=b"x" * 64_001,
            headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        )

    assert missing_origin.status_code == 403
    assert missing_origin.json() == {"error": {"code": "ORIGIN_FORBIDDEN"}}
    assert cross_site.status_code == 403
    assert cross_site.json() == {"error": {"code": "CROSS_SITE_FORBIDDEN"}}
    assert admitted.status_code == 405
    assert wrong_type.status_code == 415
    assert wrong_type.json() == {"error": {"code": "JSON_REQUIRED"}}
    assert oversized.status_code == 413
    assert oversized.json() == {"error": {"code": "REQUEST_TOO_LARGE"}}


def test_lifespan_starts_and_closes_independent_services_without_regressing_gateway() -> None:
    events: list[str] = []

    class DatabaseLifecycle:
        async def start(self) -> None:
            events.append("database:start")

        async def close(self) -> None:
            events.append("database:close")

    class MarketLifecycle:
        async def startup(self) -> None:
            events.append("market:start")

        async def aclose(self) -> None:
            events.append("market:close")

    class GatewayLifecycle:
        async def close(self) -> None:
            events.append("gateway:close")

    app = create_app(
        AppServices(
            discovery=FakeDiscovery(),
            mcp_probe=FakeProbe(),
            gateway=cast(object, GatewayLifecycle()),
            database=cast(object, DatabaseLifecycle()),
            market_data=cast(object, MarketLifecycle()),
        )
    )
    with TestClient(app, base_url=BASE_URL) as client:
        assert client.get("/api/v1/system/status").status_code == 200
        assert events == ["database:start", "market:start"]

    assert events == [
        "database:start",
        "market:start",
        "market:close",
        "database:close",
        "gateway:close",
    ]


def test_default_lifespan_builds_fresh_functional_services_for_every_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe_portfolio.api import app as app_module

    events: list[str] = []
    generations = 0

    class GenerationDiscovery(FakeDiscovery):
        def __init__(self, generation: int) -> None:
            self.generation = generation

        async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
            report = await super().discover(mcp_status)
            return report.model_copy(update={"reasons": [f"generation-{self.generation}"]})

    class Lifecycle:
        def __init__(self, generation: int, name: str) -> None:
            self.generation = generation
            self.name = name

        async def start(self) -> None:
            events.append(f"{self.generation}:{self.name}:start")

        async def startup(self) -> None:
            events.append(f"{self.generation}:{self.name}:start")

        async def close(self) -> None:
            events.append(f"{self.generation}:{self.name}:close")

        async def aclose(self) -> None:
            events.append(f"{self.generation}:{self.name}:close")

    def factory(_: object) -> AppServices:
        nonlocal generations
        generations += 1
        generation = generations
        return AppServices(
            discovery=GenerationDiscovery(generation),
            mcp_probe=FakeProbe(),
            gateway=cast(object, Lifecycle(generation, "gateway")),
            database=cast(object, Lifecycle(generation, "database")),
            market_data=cast(object, Lifecycle(generation, "market")),
        )

    monkeypatch.setattr(app_module, "build_services", factory)
    app = create_app()
    observed: list[str] = []
    for _ in range(2):
        with TestClient(app, base_url=BASE_URL) as client:
            response = client.get("/api/v1/system/compatibility")
            observed.append(response.json()["reasons"][0])

    assert observed == ["generation-1", "generation-2"]
    assert events == [
        "1:database:start",
        "1:market:start",
        "1:market:close",
        "1:database:close",
        "1:gateway:close",
        "2:database:start",
        "2:market:start",
        "2:market:close",
        "2:database:close",
        "2:gateway:close",
    ]


def test_partial_startup_failure_closes_constructed_services_in_order() -> None:
    events: list[str] = []

    class DatabaseLifecycle:
        async def start(self) -> None:
            events.append("database:start")

        async def close(self) -> None:
            events.append("database:close")

    class MarketLifecycle:
        async def startup(self) -> None:
            events.append("market:start")
            raise RuntimeError("startup-must-not-leak")

        async def aclose(self) -> None:
            events.append("market:close")

    class GatewayLifecycle:
        async def close(self) -> None:
            events.append("gateway:close")

    def factory() -> AppServices:
        return AppServices(
            discovery=FakeDiscovery(),
            mcp_probe=FakeProbe(),
            gateway=cast(object, GatewayLifecycle()),
            database=cast(object, DatabaseLifecycle()),
            market_data=cast(object, MarketLifecycle()),
        )

    app = create_app(service_factory=factory)
    with pytest.raises(RuntimeError, match="startup-must-not-leak"):
        with TestClient(app, base_url=BASE_URL):
            pass

    assert events == [
        "database:start",
        "market:start",
        "market:close",
        "database:close",
        "gateway:close",
    ]


def test_close_failures_do_not_skip_later_services_and_are_all_retrieved() -> None:
    events: list[str] = []

    class Lifecycle:
        def __init__(self, name: str, *, failure: bool = False) -> None:
            self.name = name
            self.failure = failure

        async def start(self) -> None:
            events.append(f"{self.name}:start")

        async def startup(self) -> None:
            events.append(f"{self.name}:start")

        async def close(self) -> None:
            events.append(f"{self.name}:close")
            if self.failure:
                raise RuntimeError(f"{self.name}-failed")

        async def aclose(self) -> None:
            await self.close()

    def factory() -> AppServices:
        return AppServices(
            discovery=FakeDiscovery(),
            mcp_probe=FakeProbe(),
            gateway=cast(object, Lifecycle("gateway", failure=True)),
            database=cast(object, Lifecycle("database", failure=True)),
            market_data=cast(object, Lifecycle("market", failure=True)),
        )

    app = create_app(service_factory=factory)
    with pytest.raises(BaseExceptionGroup) as caught:
        with TestClient(app, base_url=BASE_URL):
            pass

    assert events == [
        "database:start",
        "market:start",
        "market:close",
        "database:close",
        "gateway:close",
    ]
    assert {str(error) for error in caught.value.exceptions} == {
        "market-failed",
        "database-failed",
        "gateway-failed",
    }


async def test_repeated_cancellation_waits_for_every_close_and_preserves_first_cancel() -> None:
    events: list[str] = []
    entered = asyncio.Event()
    started = asyncio.Event()
    release_market = asyncio.Event()
    body_wait = asyncio.Event()

    class DatabaseLifecycle:
        async def start(self) -> None:
            events.append("database:start")

        async def close(self) -> None:
            events.append("database:close")

    class MarketLifecycle:
        async def startup(self) -> None:
            events.append("market:start")
            started.set()

        async def aclose(self) -> None:
            events.append("market:close:start")
            entered.set()
            await release_market.wait()
            events.append("market:close:end")

    class GatewayLifecycle:
        async def close(self) -> None:
            events.append("gateway:close")

    app = create_app(
        service_factory=lambda: AppServices(
            discovery=FakeDiscovery(),
            mcp_probe=FakeProbe(),
            gateway=cast(object, GatewayLifecycle()),
            database=cast(object, DatabaseLifecycle()),
            market_data=cast(object, MarketLifecycle()),
        )
    )

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            await body_wait.wait()

    task = asyncio.create_task(run_lifespan())
    await started.wait()
    task.cancel("first-cancel")
    await entered.wait()
    task.cancel("second-cancel")
    await asyncio.sleep(0)
    assert not task.done()
    release_market.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert caught.value.args == ("first-cancel",)
    assert events == [
        "database:start",
        "market:start",
        "market:close:start",
        "market:close:end",
        "database:close",
        "gateway:close",
    ]
