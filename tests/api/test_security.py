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
