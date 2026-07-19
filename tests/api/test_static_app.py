import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vibe_portfolio.api import main as api_main
from vibe_portfolio.api.app import AppServices, create_app
from vibe_portfolio.compatibility import AnalysisMode, CompatibilityReport, CompatibilityState, McpStatus
from vibe_portfolio.vibe.mcp_probe import McpProbeResult
from vibe_portfolio.web import web_dist_path

BASE_URL = "http://127.0.0.1:8765"


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


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Portfolio</title>", encoding="utf-8")
    (assets / "app-C0FFEE12.js").write_text("console.log('safe')", encoding="utf-8")
    (assets / "logo.svg").write_text("<svg></svg>", encoding="utf-8")
    return root


def static_app(static_dir: Path) -> FastAPI:
    return create_app(
        AppServices(
            discovery=FakeDiscovery(),
            mcp_probe=FakeProbe(),
            static_dir=static_dir,
        )
    )


@pytest.mark.parametrize("path", ["/", "/holdings", "/settings", "/future/client/route"])
def test_get_only_extensionless_spa_routes_serve_index(static_dir: Path, path: str) -> None:
    with TestClient(static_app(static_dir), base_url=BASE_URL) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.text == "<!doctype html><title>Portfolio</title>"


def test_hashed_assets_are_immutable_but_unhashed_assets_are_not(static_dir: Path) -> None:
    with TestClient(static_app(static_dir), base_url=BASE_URL) as client:
        hashed = client.get("/assets/app-C0FFEE12.js")
        unhashed = client.get("/assets/logo.svg")

    assert hashed.status_code == unhashed.status_code == 200
    assert hashed.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert unhashed.headers["Cache-Control"] == "no-store"


def test_unknown_api_and_asset_like_paths_never_receive_spa_html(static_dir: Path) -> None:
    with TestClient(static_app(static_dir), base_url=BASE_URL) as client:
        api_root = client.get("/api")
        asset_root = client.get("/assets")
        unknown_api = client.get("/api/v1/does-not-exist")
        missing_asset = client.get("/assets/missing.js")
        dotted_fallback = client.get("/favicon.ico")

    assert api_root.status_code == asset_root.status_code == 404
    assert unknown_api.status_code == missing_asset.status_code == dotted_fallback.status_code == 404
    assert api_root.headers["content-type"].startswith("application/json")
    assert api_root.headers["Cache-Control"] == "no-store"
    assert unknown_api.headers["content-type"].startswith("application/json")
    assert unknown_api.json() == {"detail": "Not Found"}
    assert "Portfolio" not in missing_asset.text
    assert "Portfolio" not in dotted_fallback.text


def test_non_get_requests_do_not_enter_spa_or_asset_fallback(static_dir: Path) -> None:
    headers = {"Origin": BASE_URL, "Content-Type": "application/json"}
    with TestClient(static_app(static_dir), base_url=BASE_URL) as client:
        client_route = client.post("/holdings", content=b"{}", headers=headers)
        asset = client.post("/assets/app-C0FFEE12.js", content=b"{}", headers=headers)

    assert client_route.status_code == 405
    assert asset.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/assets/%2e%2e/index.html",
        "/assets/%2e%2e%2findex.html",
        "/assets/..%5cindex.html",
    ],
)
def test_traversal_like_asset_paths_are_rejected(static_dir: Path, path: str) -> None:
    with TestClient(static_app(static_dir), base_url=BASE_URL) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert "Portfolio" not in response.text


def test_web_dist_path_is_package_relative(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    resolved = web_dist_path()

    assert resolved == Path(__file__).parents[2] / "src/vibe_portfolio/web/dist"


def test_production_main_requires_index_and_uses_configured_loopback_host_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("ok", encoding="utf-8")
    called: dict[str, object] = {}
    sentinel = object()

    class RuntimeSettings:
        api_host = "127.0.0.1"
        api_port = 9123

    monkeypatch.setattr(api_main, "Settings", RuntimeSettings)
    monkeypatch.setattr(api_main, "web_dist_path", lambda: dist)
    monkeypatch.setattr(api_main, "create_app", lambda: sentinel)
    monkeypatch.setattr(api_main.uvicorn, "run", lambda app, **kwargs: called.update(app=app, **kwargs))

    api_main.main()

    assert called == {"app": sentinel, "host": "127.0.0.1", "port": 9123, "log_level": "info"}


def test_production_main_fails_before_uvicorn_when_frontend_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def run(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(api_main, "web_dist_path", lambda: tmp_path / "missing")
    monkeypatch.setattr(api_main.uvicorn, "run", run)

    with pytest.raises(RuntimeError, match="frontend build is missing"):
        api_main.main()

    assert called is False


def test_openapi_export_is_full_and_byte_deterministic(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(Path(__file__).parents[2] / "scripts/export_openapi.py"))
    export_openapi = cast(Callable[[Path], None], namespace["export_openapi"])
    output = tmp_path / "frontend/openapi.json"
    export_openapi(output)
    first = output.read_bytes()
    export_openapi(output)

    document = json.loads(first)
    assert first == output.read_bytes()
    assert list(document) == sorted(document)
    assert "/api/v1/accounts" in document["paths"]
    assert "/api/v1/instruments/search" in document["paths"]
    assert "/api/v1/market-data/refresh" in document["paths"]
    assert "/api/v1/system/status" in document["paths"]
