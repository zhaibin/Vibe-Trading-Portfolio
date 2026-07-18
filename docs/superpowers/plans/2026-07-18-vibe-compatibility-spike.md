# Milestone 0 Vibe Compatibility Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a separately deployable compatibility spike that proves Vibe-Trading version/capability negotiation, research-only Session orchestration, resilient SSE/polling/cancellation, and an operator-installed read-only Portfolio MCP boundary without modifying Vibe-Trading.

**Architecture:** `VibeGateway` is the only Vibe-specific adapter and talks only to public REST, OpenAPI, and SSE endpoints. A loopback-only FastMCP server exposes one read-only compatibility tool, while a Sidecar system API reports `compatible`, `degraded`, or `unsupported` and fails closed for unknown contracts. This milestone deliberately contains no ledger, broker import, recommendation, scheduler, frontend, or trading implementation.

**Tech Stack:** Python 3.11+, uv, FastAPI, httpx, Pydantic v2, FastMCP 2.x, packaging, pytest, pytest-asyncio, respx, Ruff, mypy, GitHub Actions.

## Global Constraints

- Work only in the independent `Vibe-Trading-Portfolio` repository; do not modify any file in `Vibe-Trading`.
- Do not share a database, import Vibe-Trading internal Python modules, depend on Vibe-Trading file paths, or patch its runtime.
- Integrate only through Vibe-Trading public REST, OpenAPI, SSE, and operator-approved MCP configuration.
- Never set or recommend `ALLOW_SESSION_MCP_SERVERS=1`; Session payloads must never contain `mcpServers` or `mcp_servers`.
- The MCP listener must bind to `127.0.0.1`, require a dedicated bearer token, expose read-only tools only, and use an explicit `enabledTools` allowlist without `"*"`.
- Vibe version `0.1.11` at commit `67a393e4574865e8ab9b1b3f9a9fd1d7ab337343` is the initial minimum and stable contract baseline; `origin/main` is the moving latest check.
- Supported Vibe versions are initially `>=0.1.11,<0.2.0`; any version outside that range or any missing required route is `unsupported` and disables deep analysis.
- Vibe unavailable or not ready is `degraded` with analysis disabled; MCP missing or not yet verified is `degraded` with bounded-context analysis only.
- Agent messages are capped at 4,000 characters, use `risk_tier="research_general"`, and explicitly prohibit order placement, broker writes, and trade execution.
- No automatic modification of `~/.vibe-trading/agent.json`; generate a separate owner-readable snippet and require manual review/merge.
- Use TDD for every task. Do not add MVP ledger, CSV import, risk profile, analytics, recommendation, scheduler, frontend, or persistence work to this plan.

---

## Milestone Acceptance Contract

The spike is complete only when all of the following are true:

1. A supported Vibe instance reports its version and every required public route through `/api` and `/openapi.json`.
2. Unknown versions and missing routes fail closed without affecting local Sidecar health.
3. One research-only Session can be created, messaged, observed over SSE, reconnected with the same Session, polled as fallback, and cancelled.
4. The generated Vibe operator snippet contains one `streamableHttp` server, one bearer token header, and exactly `portfolio_get_capabilities` in `enabledTools`.
5. A live MCP probe observes Vibe SSE `tool_call` and successful `tool_result` events for `mcp_portfolio_portfolio_get_capabilities`.
6. All local tests pass with network access disabled; live contract tests are opt-in.
7. The upstream checkout remains unchanged and independently upgradeable.

## Source Contract References

- Vibe `0.1.11` system metadata and probes: [`agent/src/api/system_routes.py`](https://github.com/zhaibin/Vibe-Trading/blob/67a393e4574865e8ab9b1b3f9a9fd1d7ab337343/agent/src/api/system_routes.py).
- Vibe Session, goal, message, event, polling, and cancellation routes: [`agent/src/api/sessions_routes.py`](https://github.com/zhaibin/Vibe-Trading/blob/67a393e4574865e8ab9b1b3f9a9fd1d7ab337343/agent/src/api/sessions_routes.py).
- Vibe one-shot SSE tickets: [`agent/src/api/auth_routes.py`](https://github.com/zhaibin/Vibe-Trading/blob/67a393e4574865e8ab9b1b3f9a9fd1d7ab337343/agent/src/api/auth_routes.py).
- Vibe MCP operator schema and Session sanitization: [`schema.py`](https://github.com/zhaibin/Vibe-Trading/blob/67a393e4574865e8ab9b1b3f9a9fd1d7ab337343/agent/src/config/schema.py) and [`loader.py`](https://github.com/zhaibin/Vibe-Trading/blob/67a393e4574865e8ab9b1b3f9a9fd1d7ab337343/agent/src/config/loader.py).
- FastMCP 2.x server APIs: [HTTP deployment](https://gofastmcp.com/v2/deployment/http), [token verification](https://gofastmcp.com/v2/servers/auth/token-verification), and [read-only tool annotations](https://gofastmcp.com/v2/servers/tools).

## File Map

```text
Vibe-Trading-Portfolio/
├── .env.example                         # Non-secret runtime settings example
├── .gitignore                           # Python, uv, runtime-secret, and test artifacts
├── README.md                            # Scope, local setup, and operator entry points
├── pyproject.toml                       # Package, dependency, tool, and test configuration
├── uv.lock                              # Generated deterministic dependency lock
├── compatibility/baseline.json          # Pinned Vibe version/ref/route baseline
├── docs/runbooks/vibe-compatibility.md  # Operator setup, probe, and recovery runbook
├── src/vibe_portfolio/
│   ├── __init__.py                      # Package version
│   ├── config.py                        # Typed environment settings and secret handling
│   ├── compatibility.py                 # Capability matrix and state negotiation
│   ├── api/app.py                       # Sidecar FastAPI application factory
│   ├── api/main.py                      # Loopback uvicorn entry point
│   ├── cli/compatibility.py             # Machine-readable compatibility command
│   ├── mcp/install.py                   # Manual-install bundle generator
│   ├── mcp/server.py                    # Authenticated read-only FastMCP server
│   └── vibe/
│       ├── errors.py                    # Stable upstream error taxonomy
│       ├── gateway.py                   # Vibe REST/SSE adapter
│       ├── mcp_probe.py                 # Explicit end-to-end MCP invocation probe
│       ├── models.py                    # Public contract DTOs
│       ├── research.py                  # Research-only Session coordinator
│       ├── sse.py                       # Dependency-free SSE frame parser
│       └── watcher.py                   # Reconnect and polling fallback
├── tests/
│   ├── api/test_system_api.py
│   ├── contract/test_live_vibe_contract.py
│   ├── mcp/test_install.py
│   ├── mcp/test_server.py
│   ├── vibe/test_compatibility.py
│   ├── vibe/test_gateway.py
│   ├── vibe/test_mcp_probe.py
│   └── vibe/test_watcher.py
└── .github/workflows/
    ├── ci.yml                            # Hermetic unit/API/MCP checks
    └── upstream-compatibility.yml        # Minimum/stable/latest Vibe contract checks
```

### Task 1: Package Scaffold and Safe Runtime Configuration

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/vibe_portfolio/__init__.py`
- Create: `src/vibe_portfolio/config.py`
- Create: `tests/test_config.py`
- Generate: `uv.lock`

**Interfaces:**
- Consumes: no earlier implementation.
- Produces: `Settings`, `Settings.vibe_auth_headers()`, and `Settings.vibe_base_url_text()` for all later tasks.

- [ ] **Step 1: Write the failing configuration tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from vibe_portfolio.config import Settings


def test_settings_use_loopback_defaults_and_hide_secrets() -> None:
    settings = Settings(vibe_api_key="vibe-secret")

    assert settings.vibe_base_url_text() == "http://127.0.0.1:8899"
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8766
    assert settings.vibe_auth_headers() == {"Authorization": "Bearer vibe-secret"}
    assert "vibe-secret" not in repr(settings)


def test_mcp_listener_cannot_be_configured_non_loopback() -> None:
    with pytest.raises(ValidationError):
        Settings(mcp_host="0.0.0.0")


def test_message_limit_and_token_path_are_explicit() -> None:
    settings = Settings(mcp_token_file=Path("var/test-token"))

    assert settings.vibe_message_limit == 4000
    assert settings.mcp_token_file == Path("var/test-token")
```

- [ ] **Step 2: Run the test to verify the scaffold is absent**

Run: `uv run pytest tests/test_config.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'vibe_portfolio'`.

- [ ] **Step 3: Add package metadata, dependencies, and tool configuration**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "vibe-trading-portfolio"
version = "0.1.0"
description = "Personal portfolio sidecar for Vibe-Trading"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115,<1",
  "fastmcp>=2.14,<3",
  "httpx>=0.28,<1",
  "packaging>=24,<27",
  "pydantic>=2.10,<3",
  "pydantic-settings>=2.7,<3",
  "uvicorn[standard]>=0.34,<1",
]

[project.optional-dependencies]
dev = [
  "mypy>=1.14,<2",
  "pytest>=8,<10",
  "pytest-asyncio>=0.25,<2",
  "pytest-cov>=6,<8",
  "respx>=0.22,<1",
  "ruff>=0.9,<1",
]

[project.scripts]
portfolio-api = "vibe_portfolio.api.main:main"
portfolio-mcp = "vibe_portfolio.mcp.server:main"
portfolio-generate-vibe-config = "vibe_portfolio.mcp.install:main"
portfolio-compat-check = "vibe_portfolio.cli.compatibility:main"

[tool.hatch.build.targets.wheel]
packages = ["src/vibe_portfolio"]

[tool.pytest.ini_options]
addopts = "-ra --strict-markers"
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
  "contract: opt-in tests against a running Vibe-Trading instance",
]

[tool.ruff]
target-version = "py311"
line-length = 120
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]

[tool.mypy]
python_version = "3.11"
strict = true
mypy_path = "src"
```

Create the initial `README.md` so the package metadata is buildable from the first commit:

```markdown
# Vibe-Trading Portfolio

Independent personal-portfolio Sidecar for Vibe-Trading. Milestone 0 validates only the public compatibility boundary; see the approved design under `docs/superpowers/specs/`.
```

Create `.gitignore`:

```gitignore
.DS_Store
.env
.mypy_cache/
.pytest_cache/
.ruff_cache/
.venv/
__pycache__/
*.py[cod]
htmlcov/
.coverage
dist/
build/
var/
```

Create `.env.example`:

```dotenv
PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:8899
PORTFOLIO_VIBE_API_KEY=
PORTFOLIO_VIBE_CONNECT_TIMEOUT_SECONDS=3
PORTFOLIO_VIBE_READ_TIMEOUT_SECONDS=60
PORTFOLIO_VIBE_ANALYSIS_TIMEOUT_SECONDS=300
PORTFOLIO_VIBE_POLL_INTERVAL_SECONDS=1
PORTFOLIO_MCP_HOST=127.0.0.1
PORTFOLIO_MCP_PORT=8766
PORTFOLIO_MCP_TOKEN_FILE=var/secrets/mcp-token
```

Create `src/vibe_portfolio/__init__.py`:

```python
"""Vibe-Trading Portfolio sidecar."""

__version__ = "0.1.0"
```

Create `src/vibe_portfolio/config.py`:

```python
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Sidecar settings loaded from PORTFOLIO_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vibe_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8899")
    vibe_api_key: SecretStr | None = None
    vibe_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    vibe_read_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    vibe_analysis_timeout_seconds: float = Field(default=300.0, gt=0, le=1800)
    vibe_poll_interval_seconds: float = Field(default=1.0, gt=0, le=10)
    vibe_message_limit: int = Field(default=4000, ge=1, le=4000)

    mcp_host: Literal["127.0.0.1"] = "127.0.0.1"
    mcp_port: int = Field(default=8766, ge=1024, le=65535)
    mcp_token_file: Path = Path("var/secrets/mcp-token")

    def vibe_base_url_text(self) -> str:
        """Return a normalized base URL without a trailing slash."""
        return str(self.vibe_base_url).rstrip("/")

    def vibe_auth_headers(self) -> dict[str, str]:
        """Return a Bearer header only when a Vibe API key is configured."""
        if self.vibe_api_key is None:
            return {}
        token = self.vibe_api_key.get_secret_value().strip()
        return {"Authorization": f"Bearer {token}"} if token else {}
```

- [ ] **Step 4: Resolve and lock dependencies**

Run: `uv lock && uv sync --extra dev`

Expected: `uv.lock` is created, `.venv` is synchronized, and the command exits 0.

- [ ] **Step 5: Run the focused and static checks**

Run: `uv run pytest tests/test_config.py -q && uv run ruff check src tests && uv run mypy src`

Expected: `3 passed`; Ruff and mypy both exit 0.

- [ ] **Step 6: Commit the scaffold**

```bash
git add .gitignore .env.example README.md pyproject.toml uv.lock src/vibe_portfolio/__init__.py src/vibe_portfolio/config.py tests/test_config.py
git commit -m "chore: scaffold portfolio sidecar"
```

### Task 2: Typed Vibe REST Gateway and Stable Error Mapping

**Files:**
- Create: `src/vibe_portfolio/vibe/__init__.py`
- Create: `src/vibe_portfolio/vibe/errors.py`
- Create: `src/vibe_portfolio/vibe/models.py`
- Create: `src/vibe_portfolio/vibe/gateway.py`
- Create: `tests/vibe/test_gateway.py`

**Interfaces:**
- Consumes: `Settings.vibe_base_url_text()` and `Settings.vibe_auth_headers()`.
- Produces: `VibeGateway`, `GatewayError`, `GatewayErrorCode`, and typed DTO methods used by compatibility discovery, watching, research, and MCP probing.

- [ ] **Step 1: Write failing REST contract tests**

Create `tests/vibe/test_gateway.py`:

```python
import httpx
import pytest
import respx

from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.gateway import VibeGateway


@pytest.fixture
def gateway() -> VibeGateway:
    return VibeGateway(Settings(vibe_api_key="test-key"))


@respx.mock
async def test_reads_api_info_and_openapi(gateway: VibeGateway) -> None:
    api_route = respx.get("http://127.0.0.1:8899/api").mock(
        return_value=httpx.Response(200, json={"service": "Vibe-Trading API", "version": "0.1.11", "docs": "/docs", "health": "/health"})
    )
    spec_route = respx.get("http://127.0.0.1:8899/openapi.json").mock(
        return_value=httpx.Response(200, json={"openapi": "3.1.0", "paths": {"/sessions": {"post": {}}}})
    )

    assert (await gateway.api_info()).version == "0.1.11"
    assert "/sessions" in (await gateway.openapi())["paths"]
    assert api_route.calls[0].request.headers["Authorization"] == "Bearer test-key"
    assert spec_route.called
    await gateway.close()


@respx.mock
async def test_research_calls_use_public_contract_and_fixed_risk_tier(gateway: VibeGateway) -> None:
    session_route = respx.post("http://127.0.0.1:8899/sessions").mock(
        return_value=httpx.Response(
            201,
            json={
                "session_id": "session-1",
                "title": "Portfolio compatibility probe",
                "status": "active",
                "created_at": "2026-07-18T00:00:00Z",
                "updated_at": "2026-07-18T00:00:00Z",
                "last_attempt_id": None,
            },
        )
    )
    goal_route = respx.post("http://127.0.0.1:8899/sessions/session-1/goal").mock(
        return_value=httpx.Response(201, json={"goal": {"goal_id": "goal-1"}, "claims": [], "criteria": [], "evidence": [], "evidence_count": 0})
    )
    message_route = respx.post("http://127.0.0.1:8899/sessions/session-1/messages").mock(
        return_value=httpx.Response(200, json={"message_id": "message-1", "attempt_id": "attempt-1"})
    )

    session = await gateway.create_session("Portfolio compatibility probe")
    await gateway.create_research_goal(session.session_id, "Verify read-only Portfolio context", ["Call the approved Portfolio MCP tool"])
    accepted = await gateway.send_message(session.session_id, "Read-only compatibility check")

    assert accepted.attempt_id == "attempt-1"
    assert session_route.calls[0].request.content == b'{"title":"Portfolio compatibility probe","config":{}}'
    assert b'"risk_tier":"research_general"' in goal_route.calls[0].request.content
    assert b"mcpServers" not in session_route.calls[0].request.content
    assert message_route.called
    await gateway.close()


@respx.mock
async def test_maps_auth_and_offline_failures(gateway: VibeGateway) -> None:
    route = respx.get("http://127.0.0.1:8899/api").mock(return_value=httpx.Response(401, json={"detail": "invalid"}))

    with pytest.raises(GatewayError) as auth_error:
        await gateway.api_info()
    assert auth_error.value.code is GatewayErrorCode.VIBE_AUTH_FAILED

    route.mock(side_effect=httpx.ConnectError("offline"))
    with pytest.raises(GatewayError) as offline_error:
        await gateway.api_info()
    assert offline_error.value.code is GatewayErrorCode.VIBE_UNAVAILABLE
    await gateway.close()


async def test_rejects_messages_larger_than_bounded_context(gateway: VibeGateway) -> None:
    with pytest.raises(ValueError, match="4,000"):
        await gateway.send_message("session-1", "x" * 4001)
    await gateway.close()


@respx.mock
async def test_supports_poll_ticket_and_cancel_contracts(gateway: VibeGateway) -> None:
    respx.get(url__startswith="http://127.0.0.1:8899/sessions/session-1/messages").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "message_id": "message-2",
                    "session_id": "session-1",
                    "role": "assistant",
                    "content": "done",
                    "created_at": "2026-07-18T00:00:01Z",
                    "linked_attempt_id": "attempt-1",
                    "metadata": {"status": "completed"},
                }
            ],
        )
    )
    respx.post("http://127.0.0.1:8899/auth/sse-ticket").mock(
        return_value=httpx.Response(200, json={"ticket": "ticket-1"})
    )
    respx.post("http://127.0.0.1:8899/sessions/session-1/cancel").mock(
        return_value=httpx.Response(200, json={"status": "cancelled"})
    )

    assert (await gateway.list_messages("session-1"))[0].linked_attempt_id == "attempt-1"
    assert (await gateway.mint_sse_ticket()).ticket == "ticket-1"
    assert (await gateway.cancel("session-1")).status == "cancelled"
    await gateway.close()
```

- [ ] **Step 2: Run the gateway tests and confirm missing modules**

Run: `uv run pytest tests/vibe/test_gateway.py -q`

Expected: FAIL during collection because `vibe_portfolio.vibe.gateway` does not exist.

- [ ] **Step 3: Add the error taxonomy and public DTOs**

Create `src/vibe_portfolio/vibe/__init__.py`:

```python
"""Public Vibe-Trading adapter boundary."""
```

Create `src/vibe_portfolio/vibe/errors.py`:

```python
from enum import StrEnum


class GatewayErrorCode(StrEnum):
    VIBE_UNAVAILABLE = "VIBE_UNAVAILABLE"
    VIBE_TIMEOUT = "VIBE_TIMEOUT"
    VIBE_AUTH_FAILED = "VIBE_AUTH_FAILED"
    VIBE_CONTRACT_ERROR = "VIBE_CONTRACT_ERROR"
    VIBE_UPSTREAM_ERROR = "VIBE_UPSTREAM_ERROR"


class GatewayError(RuntimeError):
    """Stable Sidecar-facing error that does not leak upstream response bodies."""

    def __init__(self, code: GatewayErrorCode, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
```

Create `src/vibe_portfolio/vibe/models.py`:

```python
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ApiInfo(ContractModel):
    service: str
    version: str
    docs: str
    health: str


class ProbeResult(BaseModel):
    ok: bool
    status_code: int
    detail: str | None = None


class SessionRecord(ContractModel):
    session_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    last_attempt_id: str | None = None


class GoalSnapshot(ContractModel):
    goal: dict[str, Any]
    claims: list[dict[str, Any]] = Field(default_factory=list)
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    evidence_count: int = 0


class MessageAccepted(ContractModel):
    message_id: str
    attempt_id: str


class MessageRecord(ContractModel):
    message_id: str
    session_id: str
    role: str
    content: str
    created_at: str
    linked_attempt_id: str | None = None
    metadata: dict[str, Any] | None = None


class CancelResult(ContractModel):
    status: str


class SseTicket(ContractModel):
    ticket: str
```

- [ ] **Step 4: Implement the minimal REST gateway**

Create `src/vibe_portfolio/vibe/gateway.py`:

```python
from typing import Any, cast

import httpx

from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.models import (
    ApiInfo,
    CancelResult,
    GoalSnapshot,
    MessageAccepted,
    MessageRecord,
    ProbeResult,
    SessionRecord,
    SseTicket,
)


class VibeGateway:
    """The only component allowed to know Vibe-Trading HTTP details."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.vibe_base_url_text(),
            headers=settings.vibe_auth_headers(),
            timeout=httpx.Timeout(
                connect=settings.vibe_connect_timeout_seconds,
                read=settings.vibe_read_timeout_seconds,
                write=settings.vibe_read_timeout_seconds,
                pool=settings.vibe_connect_timeout_seconds,
            ),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, *, expected: set[int] | None = None, **kwargs: Any) -> httpx.Response:
        accepted = expected or {200}
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise GatewayError(GatewayErrorCode.VIBE_TIMEOUT, "Vibe-Trading request timed out") from exc
        except httpx.RequestError as exc:
            raise GatewayError(GatewayErrorCode.VIBE_UNAVAILABLE, "Vibe-Trading is unavailable") from exc
        if response.status_code in accepted:
            return response
        self._raise_response_error(response)
        raise AssertionError("unreachable")

    @staticmethod
    def _raise_response_error(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise GatewayError(GatewayErrorCode.VIBE_AUTH_FAILED, "Vibe-Trading authentication failed", response.status_code)
        if response.status_code in {404, 405, 422, 501}:
            raise GatewayError(GatewayErrorCode.VIBE_CONTRACT_ERROR, "Vibe-Trading public contract is incompatible", response.status_code)
        raise GatewayError(GatewayErrorCode.VIBE_UPSTREAM_ERROR, "Vibe-Trading returned an upstream error", response.status_code)

    async def api_info(self) -> ApiInfo:
        response = await self._request("GET", "/api")
        return ApiInfo.model_validate(response.json())

    async def openapi(self) -> dict[str, Any]:
        response = await self._request("GET", "/openapi.json")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
            raise GatewayError(GatewayErrorCode.VIBE_CONTRACT_ERROR, "Vibe-Trading OpenAPI document has no paths")
        return cast(dict[str, Any], payload)

    async def live(self) -> ProbeResult:
        response = await self._request("GET", "/live")
        return ProbeResult(ok=True, status_code=response.status_code)

    async def ready(self) -> ProbeResult:
        response = await self._request("GET", "/ready", expected={200, 503})
        detail: str | None = None
        if response.status_code == 503:
            body = response.json()
            detail = str(body.get("detail", "not ready")) if isinstance(body, dict) else "not ready"
        return ProbeResult(ok=response.status_code == 200, status_code=response.status_code, detail=detail)

    async def create_session(self, title: str) -> SessionRecord:
        response = await self._request("POST", "/sessions", expected={201}, json={"title": title, "config": {}})
        return SessionRecord.model_validate(response.json())

    async def create_research_goal(self, session_id: str, objective: str, criteria: list[str]) -> GoalSnapshot:
        payload = {
            "objective": objective,
            "criteria": criteria,
            "ui_summary": objective[:500],
            "protocol": "thesis_review",
            "risk_tier": "research_general",
        }
        response = await self._request("POST", f"/sessions/{session_id}/goal", expected={201}, json=payload)
        return GoalSnapshot.model_validate(response.json())

    async def send_message(self, session_id: str, content: str) -> MessageAccepted:
        if len(content) > self.settings.vibe_message_limit:
            raise ValueError("Vibe Session messages must not exceed the bounded 4,000 character context")
        response = await self._request("POST", f"/sessions/{session_id}/messages", json={"content": content})
        return MessageAccepted.model_validate(response.json())

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        response = await self._request("GET", f"/sessions/{session_id}/messages", params={"limit": limit})
        return [MessageRecord.model_validate(item) for item in response.json()]

    async def cancel(self, session_id: str) -> CancelResult:
        response = await self._request("POST", f"/sessions/{session_id}/cancel")
        return CancelResult.model_validate(response.json())

    async def mint_sse_ticket(self) -> SseTicket:
        response = await self._request("POST", "/auth/sse-ticket")
        return SseTicket.model_validate(response.json())
```

- [ ] **Step 5: Run focused tests and static checks**

Run: `uv run pytest tests/vibe/test_gateway.py -q && uv run ruff check src/vibe_portfolio/vibe tests/vibe/test_gateway.py && uv run mypy src`

Expected: `5 passed`; Ruff and mypy exit 0.

- [ ] **Step 6: Commit the gateway boundary**

```bash
git add src/vibe_portfolio/vibe tests/vibe/test_gateway.py
git commit -m "feat: add typed vibe gateway"
```

### Task 3: Version and Capability Negotiation

**Files:**
- Create: `src/vibe_portfolio/compatibility.py`
- Create: `tests/vibe/test_compatibility.py`

**Interfaces:**
- Consumes: `VibeGateway.api_info()`, `openapi()`, `live()`, and `ready()`.
- Produces: `CompatibilityDiscovery.discover(mcp_status) -> CompatibilityReport`, `CompatibilityState`, `AnalysisMode`, `McpStatus`, and `REQUIRED_ENDPOINTS`.

- [ ] **Step 1: Write the compatibility state matrix tests**

Create `tests/vibe/test_compatibility.py`:

```python
from typing import Any

import pytest

from vibe_portfolio.compatibility import AnalysisMode, CompatibilityDiscovery, CompatibilityState, McpStatus, REQUIRED_ENDPOINTS
from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.models import ApiInfo, ProbeResult


class FakeGateway:
    def __init__(self, *, version: str = "0.1.11", paths: dict[str, Any] | None = None, ready: bool = True, offline: bool = False) -> None:
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
        return ProbeResult(ok=self.is_ready, status_code=200 if self.is_ready else 503, detail=None if self.is_ready else "LLM not configured")


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
```

- [ ] **Step 2: Run the state matrix tests and confirm the module is absent**

Run: `uv run pytest tests/vibe/test_compatibility.py -q`

Expected: FAIL during collection because `vibe_portfolio.compatibility` does not exist.

- [ ] **Step 3: Implement version and OpenAPI capability negotiation**

Create `src/vibe_portfolio/compatibility.py`:

```python
from enum import StrEnum
from typing import Protocol

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

from vibe_portfolio.vibe.errors import GatewayError
from vibe_portfolio.vibe.models import ApiInfo, ProbeResult

SUPPORTED_VIBE_VERSIONS = SpecifierSet(">=0.1.11,<0.2.0")


class CompatibilityState(StrEnum):
    COMPATIBLE = "compatible"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class AnalysisMode(StrEnum):
    FULL_MCP = "full_mcp"
    BOUNDED_CONTEXT = "bounded_context"
    DISABLED = "disabled"


class McpStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    AVAILABLE = "available"
    MISSING = "missing"
    FAILED = "failed"


REQUIRED_ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("sessions.create", "POST", "/sessions"),
    ("goals.create_research", "POST", "/sessions/{session_id}/goal"),
    ("messages.send", "POST", "/sessions/{session_id}/messages"),
    ("messages.poll", "GET", "/sessions/{session_id}/messages"),
    ("events.stream", "GET", "/sessions/{session_id}/events"),
    ("events.ticket", "POST", "/auth/sse-ticket"),
    ("sessions.cancel", "POST", "/sessions/{session_id}/cancel"),
)


class DiscoveryGateway(Protocol):
    async def api_info(self) -> ApiInfo:
        ...

    async def openapi(self) -> dict[str, object]:
        ...

    async def live(self) -> ProbeResult:
        ...

    async def ready(self) -> ProbeResult:
        ...


class CompatibilityReport(BaseModel):
    state: CompatibilityState
    analysis_mode: AnalysisMode
    contract_compatible: bool
    deep_analysis_enabled: bool
    vibe_version: str | None = None
    supported_versions: str = str(SUPPORTED_VIBE_VERSIONS)
    mcp_status: McpStatus
    missing_capabilities: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class CompatibilityDiscovery:
    def __init__(self, gateway: DiscoveryGateway) -> None:
        self.gateway = gateway

    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
        try:
            info = await self.gateway.api_info()
            spec = await self.gateway.openapi()
            await self.gateway.live()
            readiness = await self.gateway.ready()
        except GatewayError as exc:
            return CompatibilityReport(
                state=CompatibilityState.DEGRADED,
                analysis_mode=AnalysisMode.DISABLED,
                contract_compatible=False,
                deep_analysis_enabled=False,
                mcp_status=mcp_status,
                reasons=[exc.code.value.lower()],
            )

        reasons: list[str] = []
        try:
            version_supported = Version(info.version) in SUPPORTED_VIBE_VERSIONS
        except InvalidVersion:
            version_supported = False
        if not version_supported:
            reasons.append("version_out_of_range")

        raw_paths = spec.get("paths")
        paths = raw_paths if isinstance(raw_paths, dict) else {}
        missing = [
            capability
            for capability, method, path in REQUIRED_ENDPOINTS
            if not isinstance(paths.get(path), dict) or method.lower() not in paths[path]
        ]

        if not version_supported or missing:
            return CompatibilityReport(
                state=CompatibilityState.UNSUPPORTED,
                analysis_mode=AnalysisMode.DISABLED,
                contract_compatible=False,
                deep_analysis_enabled=False,
                vibe_version=info.version,
                mcp_status=mcp_status,
                missing_capabilities=missing,
                reasons=reasons + (["required_capability_missing"] if missing else []),
            )

        if not readiness.ok:
            return CompatibilityReport(
                state=CompatibilityState.DEGRADED,
                analysis_mode=AnalysisMode.DISABLED,
                contract_compatible=True,
                deep_analysis_enabled=False,
                vibe_version=info.version,
                mcp_status=mcp_status,
                reasons=["vibe_not_ready"],
            )

        if mcp_status is McpStatus.AVAILABLE:
            return CompatibilityReport(
                state=CompatibilityState.COMPATIBLE,
                analysis_mode=AnalysisMode.FULL_MCP,
                contract_compatible=True,
                deep_analysis_enabled=True,
                vibe_version=info.version,
                mcp_status=mcp_status,
            )

        mcp_reason = {
            McpStatus.NOT_CHECKED: "mcp_not_verified",
            McpStatus.MISSING: "mcp_not_configured",
            McpStatus.FAILED: "mcp_probe_failed",
        }[mcp_status]
        return CompatibilityReport(
            state=CompatibilityState.DEGRADED,
            analysis_mode=AnalysisMode.BOUNDED_CONTEXT,
            contract_compatible=True,
            deep_analysis_enabled=True,
            vibe_version=info.version,
            mcp_status=mcp_status,
            reasons=[mcp_reason],
        )
```

- [ ] **Step 4: Run the state matrix and type checks**

Run: `uv run pytest tests/vibe/test_compatibility.py -q && uv run ruff check src/vibe_portfolio/compatibility.py tests/vibe/test_compatibility.py && uv run mypy src`

Expected: `6 passed`; Ruff and mypy exit 0.

- [ ] **Step 5: Commit compatibility negotiation**

```bash
git add src/vibe_portfolio/compatibility.py tests/vibe/test_compatibility.py
git commit -m "feat: negotiate vibe compatibility"
```

### Task 4: SSE Parsing, Reconnection, and Polling Fallback

**Files:**
- Create: `src/vibe_portfolio/vibe/sse.py`
- Modify: `src/vibe_portfolio/vibe/gateway.py`
- Create: `src/vibe_portfolio/vibe/watcher.py`
- Modify: `tests/vibe/test_gateway.py`
- Create: `tests/vibe/test_watcher.py`

**Interfaces:**
- Consumes: `VibeGateway.mint_sse_ticket()` and `VibeGateway.list_messages()`.
- Produces: `SseEvent`, `iter_sse()`, `VibeGateway.stream_events()`, `AttemptWatcher.wait()`, and `AttemptOutcome`.

- [ ] **Step 1: Write failing parser, reconnect, and polling tests**

Create `tests/vibe/test_watcher.py`:

```python
from collections.abc import AsyncIterator
from typing import Any

import pytest

from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.models import MessageRecord
from vibe_portfolio.vibe.sse import SseEvent, iter_sse
from vibe_portfolio.vibe.watcher import AttemptStatus, AttemptWatcher


async def as_lines(values: list[str]) -> AsyncIterator[str]:
    for value in values:
        yield value


async def no_sleep(_: float) -> None:
    return None


async def test_parser_supports_ids_events_and_multiline_json() -> None:
    events = [
        event
        async for event in iter_sse(
            as_lines(["id: e1", "event: tool_call", 'data: {"tool":', 'data: "portfolio"}', ""])
        )
    ]

    assert events == [SseEvent(event_id="e1", event_type="tool_call", data={"tool": "portfolio"})]


class FakeWatchGateway:
    def __init__(self, streams: list[list[SseEvent] | GatewayError], messages: list[MessageRecord] | None = None) -> None:
        self.streams = streams
        self.messages = messages or []
        self.stream_calls: list[tuple[str, str | None]] = []
        self.poll_calls = 0

    async def stream_events(self, session_id: str, last_event_id: str | None = None) -> AsyncIterator[SseEvent]:
        self.stream_calls.append((session_id, last_event_id))
        current = self.streams.pop(0)
        if isinstance(current, GatewayError):
            raise current
        for event in current:
            yield event

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        self.poll_calls += 1
        return self.messages


async def test_reconnects_same_session_from_last_event_id_without_polling() -> None:
    gateway = FakeWatchGateway(
        streams=[
            [SseEvent(event_id="e1", event_type="tool_call", data={"tool": "probe"})],
            [SseEvent(event_id="e2", event_type="attempt.completed", data={"attempt_id": "attempt-1"})],
        ]
    )
    watcher = AttemptWatcher(gateway, max_reconnects=2, poll_interval_seconds=0, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.COMPLETED
    assert gateway.stream_calls == [("session-1", None), ("session-1", "e1")]
    assert gateway.poll_calls == 0
    assert outcome.used_polling is False


async def test_falls_back_to_polling_the_original_attempt() -> None:
    assistant = MessageRecord(
        message_id="message-2",
        session_id="session-1",
        role="assistant",
        content="done",
        created_at="2026-07-18T00:00:01Z",
        linked_attempt_id="attempt-1",
    )
    gateway = FakeWatchGateway(
        streams=[GatewayError(GatewayErrorCode.VIBE_UNAVAILABLE, "stream lost")],
        messages=[assistant],
    )
    watcher = AttemptWatcher(gateway, max_reconnects=0, poll_interval_seconds=0, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.COMPLETED
    assert outcome.used_polling is True
    assert outcome.assistant_message == assistant
    assert gateway.stream_calls == [("session-1", None)]


async def test_failed_terminal_event_is_not_reported_as_success() -> None:
    gateway = FakeWatchGateway(
        streams=[[SseEvent(event_id="e1", event_type="attempt.failed", data={"attempt_id": "attempt-1", "error": "provider"})]]
    )
    watcher = AttemptWatcher(gateway, max_reconnects=0, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.FAILED
    assert outcome.terminal_event is not None
    assert outcome.terminal_event.data["error"] == "provider"
```

- [ ] **Step 2: Run tests and verify the SSE module is absent**

Run: `uv run pytest tests/vibe/test_watcher.py -q`

Expected: FAIL during collection because `vibe_portfolio.vibe.sse` does not exist.

- [ ] **Step 3: Implement the SSE parser**

Create `src/vibe_portfolio/vibe/sse.py`:

```python
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SseEvent:
    event_id: str | None
    event_type: str
    data: dict[str, Any]


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """Parse SSE frames without binding the gateway to a browser library."""
    event_id: str | None = None
    event_type = "message"
    data_lines: list[str] = []

    async for line in lines:
        if line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError:
                    decoded = {"raw": raw}
                data = decoded if isinstance(decoded, dict) else {"value": decoded}
                yield SseEvent(event_id=event_id, event_type=event_type, data=data)
            event_id = None
            event_type = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            event_id = value
        elif field == "event":
            event_type = value
        elif field == "data":
            data_lines.append(value)

    if data_lines:
        raw = "\n".join(data_lines)
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = {"raw": raw}
        data = decoded if isinstance(decoded, dict) else {"value": decoded}
        yield SseEvent(event_id=event_id, event_type=event_type, data=data)
```

- [ ] **Step 4: Write the failing ticket-authenticated stream test**

Add this import to `tests/vibe/test_gateway.py`:

```python
from vibe_portfolio.vibe.sse import SseEvent
```

Append this test to `tests/vibe/test_gateway.py`:

```python
@respx.mock
async def test_stream_events_uses_ticket_replay_and_last_event_id(gateway: VibeGateway) -> None:
    respx.post("http://127.0.0.1:8899/auth/sse-ticket").mock(
        return_value=httpx.Response(200, json={"ticket": "ticket-2"})
    )
    event_route = respx.get(url__startswith="http://127.0.0.1:8899/sessions/session-1/events").mock(
        return_value=httpx.Response(
            200,
            text='id: e2\nevent: attempt.completed\ndata: {"attempt_id":"attempt-1"}\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )

    events = [event async for event in gateway.stream_events("session-1", "e1")]

    assert events == [SseEvent("e2", "attempt.completed", {"attempt_id": "attempt-1"})]
    request = event_route.calls[0].request
    assert request.url.params["ticket"] == "ticket-2"
    assert request.url.params["replay"] == "active"
    assert request.headers["Last-Event-ID"] == "e1"
    await gateway.close()
```

Run: `uv run pytest tests/vibe/test_gateway.py::test_stream_events_uses_ticket_replay_and_last_event_id -q`

Expected: FAIL with `AttributeError: 'VibeGateway' object has no attribute 'stream_events'`.

- [ ] **Step 5: Add authenticated SSE streaming to the gateway**

Append these imports to `src/vibe_portfolio/vibe/gateway.py`:

```python
from collections.abc import AsyncIterator

from vibe_portfolio.vibe.sse import SseEvent, iter_sse
```

Add this method inside `VibeGateway` after `mint_sse_ticket`:

```python
    async def stream_events(self, session_id: str, last_event_id: str | None = None) -> AsyncIterator[SseEvent]:
        """Open one ticket-authenticated stream; callers own reconnect policy."""
        ticket = await self.mint_sse_ticket()
        params = {"ticket": ticket.ticket, "replay": "active"}
        headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
        try:
            async with self._client.stream(
                "GET",
                f"/sessions/{session_id}/events",
                params=params,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    self._raise_response_error(response)
                async for event in iter_sse(response.aiter_lines()):
                    yield event
        except GatewayError:
            raise
        except httpx.TimeoutException as exc:
            raise GatewayError(GatewayErrorCode.VIBE_TIMEOUT, "Vibe-Trading SSE timed out") from exc
        except httpx.RequestError as exc:
            raise GatewayError(GatewayErrorCode.VIBE_UNAVAILABLE, "Vibe-Trading SSE disconnected") from exc
```

- [ ] **Step 6: Implement reconnect and polling fallback**

Create `src/vibe_portfolio/vibe/watcher.py`:

```python
import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from vibe_portfolio.vibe.errors import GatewayError
from vibe_portfolio.vibe.models import MessageRecord
from vibe_portfolio.vibe.sse import SseEvent


class AttemptStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class WatchGateway(Protocol):
    def stream_events(self, session_id: str, last_event_id: str | None = None) -> AsyncIterator[SseEvent]:
        ...

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        ...


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    session_id: str
    attempt_id: str
    status: AttemptStatus
    events: tuple[SseEvent, ...]
    terminal_event: SseEvent | None
    assistant_message: MessageRecord | None
    used_polling: bool


class AttemptWatcher:
    def __init__(
        self,
        gateway: WatchGateway,
        *,
        max_reconnects: int = 2,
        poll_interval_seconds: float = 1.0,
        timeout_seconds: float = 300.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gateway = gateway
        self.max_reconnects = max_reconnects
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.monotonic = monotonic

    async def wait(self, session_id: str, attempt_id: str) -> AttemptOutcome:
        deadline = self.monotonic() + self.timeout_seconds
        events: list[SseEvent] = []
        last_event_id: str | None = None

        for reconnect_index in range(self.max_reconnects + 1):
            if self.monotonic() >= deadline:
                break
            try:
                async for event in self.gateway.stream_events(session_id, last_event_id):
                    events.append(event)
                    last_event_id = event.event_id or last_event_id
                    terminal = self._terminal_status(event, attempt_id)
                    if terminal is not None:
                        return AttemptOutcome(session_id, attempt_id, terminal, tuple(events), event, None, False)
            except GatewayError:
                pass
            if reconnect_index < self.max_reconnects:
                await self.sleep(min(0.25 * (2**reconnect_index), 2.0))

        while self.monotonic() < deadline:
            for message in await self.gateway.list_messages(session_id, limit=100):
                if message.role == "assistant" and message.linked_attempt_id == attempt_id:
                    status = {
                        "failed": AttemptStatus.FAILED,
                        "cancelled": AttemptStatus.CANCELLED,
                    }.get(str((message.metadata or {}).get("status")), AttemptStatus.COMPLETED)
                    return AttemptOutcome(session_id, attempt_id, status, tuple(events), None, message, True)
            await self.sleep(self.poll_interval_seconds)

        return AttemptOutcome(session_id, attempt_id, AttemptStatus.TIMED_OUT, tuple(events), None, None, True)

    @staticmethod
    def _terminal_status(event: SseEvent, attempt_id: str) -> AttemptStatus | None:
        if event.data.get("attempt_id") != attempt_id:
            return None
        return {
            "attempt.completed": AttemptStatus.COMPLETED,
            "attempt.failed": AttemptStatus.FAILED,
            "attempt.cancelled": AttemptStatus.CANCELLED,
        }.get(event.event_type)
```

- [ ] **Step 7: Run watcher tests, gateway regression tests, and static checks**

Run: `uv run pytest tests/vibe/test_watcher.py tests/vibe/test_gateway.py -q && uv run ruff check src tests && uv run mypy src`

Expected: `10 passed`; Ruff and mypy exit 0.

- [ ] **Step 8: Commit resilient attempt observation**

```bash
git add src/vibe_portfolio/vibe/gateway.py src/vibe_portfolio/vibe/sse.py src/vibe_portfolio/vibe/watcher.py tests/vibe/test_watcher.py
git commit -m "feat: add resilient vibe event watching"
```

### Task 5: Authenticated Read-Only Portfolio MCP and Manual Install Bundle

**Files:**
- Create: `src/vibe_portfolio/mcp/__init__.py`
- Create: `src/vibe_portfolio/mcp/server.py`
- Create: `src/vibe_portfolio/mcp/install.py`
- Create: `tests/mcp/test_server.py`
- Create: `tests/mcp/test_install.py`

**Interfaces:**
- Consumes: `Settings.mcp_host`, `mcp_port`, and `mcp_token_file`.
- Produces: `build_mcp_server(token)`, `portfolio_get_capabilities()`, `create_install_bundle()`, and operator files `mcp-token` plus `vibe-portfolio-mcp-snippet.json`.

- [ ] **Step 1: Write failing MCP safety tests**

Create `tests/mcp/test_server.py`:

```python
from fastmcp import Client

from vibe_portfolio.mcp.server import MCP_TOOL_NAME, build_mcp_server, portfolio_get_capabilities


async def test_server_exposes_one_read_only_closed_world_tool() -> None:
    server = build_mcp_server("test-token")

    async with Client(server) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools] == [MCP_TOOL_NAME]
    assert tools[0].annotations is not None
    assert tools[0].annotations.readOnlyHint is True
    assert tools[0].annotations.destructiveHint is False
    assert tools[0].annotations.idempotentHint is True
    assert tools[0].annotations.openWorldHint is False


def test_capability_payload_is_structured_and_explicit() -> None:
    payload = portfolio_get_capabilities()

    assert payload == {
        "schema_version": "portfolio-mcp.v1",
        "mode": "compatibility_spike",
        "read_only": True,
        "tools": ["portfolio_get_capabilities"],
        "mutations": [],
    }
```

Create `tests/mcp/test_install.py`:

```python
import json
import stat
from pathlib import Path

import pytest

from vibe_portfolio.mcp.install import create_install_bundle


def test_bundle_is_owner_only_explicit_and_never_edits_vibe_config(tmp_path: Path) -> None:
    vibe_config = tmp_path / "agent.json"
    vibe_config.write_text('{"existing": true}\n', encoding="utf-8")
    output_dir = tmp_path / "bundle"

    bundle = create_install_bundle(output_dir, "http://127.0.0.1:8766/mcp", token="dedicated-token")
    snippet = json.loads(bundle.snippet_file.read_text(encoding="utf-8"))

    assert vibe_config.read_text(encoding="utf-8") == '{"existing": true}\n'
    assert snippet == {
        "mcpServers": {
            "portfolio": {
                "type": "streamableHttp",
                "url": "http://127.0.0.1:8766/mcp",
                "headers": {"Authorization": "Bearer dedicated-token"},
                "toolTimeout": 30.0,
                "initTimeout": 30.0,
                "enabledTools": ["portfolio_get_capabilities"],
            }
        }
    }
    assert "ALLOW_SESSION_MCP_SERVERS" not in bundle.snippet_file.read_text(encoding="utf-8")
    assert stat.S_IMODE(bundle.token_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(bundle.snippet_file.stat().st_mode) == 0o600


def test_bundle_refuses_to_overwrite_existing_secrets(tmp_path: Path) -> None:
    output_dir = tmp_path / "bundle"
    create_install_bundle(output_dir, "http://127.0.0.1:8766/mcp", token="first")

    with pytest.raises(FileExistsError):
        create_install_bundle(output_dir, "http://127.0.0.1:8766/mcp", token="second")
```

- [ ] **Step 2: Run MCP tests and verify the modules are absent**

Run: `uv run pytest tests/mcp -q`

Expected: FAIL during collection because `vibe_portfolio.mcp.server` and `install` do not exist.

- [ ] **Step 3: Implement the token-protected read-only MCP server**

Create `src/vibe_portfolio/mcp/__init__.py`:

```python
"""Read-only Portfolio MCP boundary."""
```

Create `src/vibe_portfolio/mcp/server.py`:

```python
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from mcp.types import ToolAnnotations

from vibe_portfolio.config import Settings

MCP_TOOL_NAME = "portfolio_get_capabilities"


def portfolio_get_capabilities() -> dict[str, object]:
    """Return the read-only Portfolio MCP contract supported by this spike."""
    return {
        "schema_version": "portfolio-mcp.v1",
        "mode": "compatibility_spike",
        "read_only": True,
        "tools": [MCP_TOOL_NAME],
        "mutations": [],
    }


def build_mcp_server(token: str) -> FastMCP:
    if not token.strip():
        raise ValueError("Portfolio MCP token must not be empty")
    verifier = StaticTokenVerifier(
        tokens={token: {"client_id": "vibe-trading", "scopes": ["portfolio.read"]}},
        required_scopes=["portfolio.read"],
    )
    server = FastMCP(name="Vibe-Trading Portfolio", version="0.1.0", auth=verifier)
    server.tool(
        name=MCP_TOOL_NAME,
        description="Read the Portfolio MCP compatibility contract. This tool never mutates portfolio data.",
        annotations=ToolAnnotations(
            title="Portfolio capabilities",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )(portfolio_get_capabilities)
    return server


def _read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"Portfolio MCP token file is empty: {path}")
    return token


def main() -> None:
    settings = Settings()
    server = build_mcp_server(_read_token(settings.mcp_token_file))
    server.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path="/mcp",
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement the review-before-install bundle generator**

Create `src/vibe_portfolio/mcp/install.py`:

```python
import argparse
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from vibe_portfolio.mcp.server import MCP_TOOL_NAME


@dataclass(frozen=True, slots=True)
class InstallBundle:
    token_file: Path
    snippet_file: Path


def _write_owner_only(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def create_install_bundle(output_dir: Path, mcp_url: str, *, token: str | None = None) -> InstallBundle:
    if not mcp_url.startswith("http://127.0.0.1:"):
        raise ValueError("Milestone 0 MCP URL must use loopback http://127.0.0.1")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    token_file = output_dir / "mcp-token"
    snippet_file = output_dir / "vibe-portfolio-mcp-snippet.json"
    if token_file.exists() or snippet_file.exists():
        raise FileExistsError("Install bundle already exists; remove or archive it explicitly before rotating the token")

    dedicated_token = token or secrets.token_urlsafe(32)
    snippet = {
        "mcpServers": {
            "portfolio": {
                "type": "streamableHttp",
                "url": mcp_url,
                "headers": {"Authorization": f"Bearer {dedicated_token}"},
                "toolTimeout": 30.0,
                "initTimeout": 30.0,
                "enabledTools": [MCP_TOOL_NAME],
            }
        }
    }
    _write_owner_only(token_file, f"{dedicated_token}\n")
    _write_owner_only(snippet_file, json.dumps(snippet, ensure_ascii=False, indent=2) + "\n")
    return InstallBundle(token_file=token_file, snippet_file=snippet_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a manually reviewed Vibe Portfolio MCP configuration snippet")
    parser.add_argument("--output-dir", type=Path, default=Path("var/install"))
    parser.add_argument("--url", default="http://127.0.0.1:8766/mcp")
    args = parser.parse_args()
    bundle = create_install_bundle(args.output_dir, args.url)
    print(f"Token file: {bundle.token_file}")
    print(f"Review and manually merge: {bundle.snippet_file}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run MCP tests and static checks**

Run: `uv run pytest tests/mcp -q && uv run ruff check src tests && uv run mypy src`

Expected: `4 passed`; Ruff and mypy exit 0.

- [ ] **Step 6: Commit the MCP boundary**

```bash
git add src/vibe_portfolio/mcp tests/mcp
git commit -m "feat: add read-only portfolio mcp"
```

### Task 6: Research-Only Session Coordinator and Verifiable MCP Probe

**Files:**
- Create: `src/vibe_portfolio/vibe/research.py`
- Create: `src/vibe_portfolio/vibe/mcp_probe.py`
- Create: `tests/vibe/test_mcp_probe.py`

**Interfaces:**
- Consumes: `VibeGateway` Session/goal/message/cancel methods and `AttemptWatcher.wait()`.
- Produces: `ResearchCoordinator.start() -> StartedResearch`, `PortfolioMcpProbe.run() -> McpProbeResult`, and exact observed tool-event validation.

- [ ] **Step 1: Write failing coordinator and MCP proof tests**

Create `tests/vibe/test_mcp_probe.py`:

```python
from typing import Any

from vibe_portfolio.compatibility import McpStatus
from vibe_portfolio.vibe.mcp_probe import EXPECTED_VIBE_TOOL_NAME, PortfolioMcpProbe
from vibe_portfolio.vibe.models import CancelResult, GoalSnapshot, MessageAccepted, SessionRecord
from vibe_portfolio.vibe.sse import SseEvent
from vibe_portfolio.vibe.watcher import AttemptOutcome, AttemptStatus


class FakeGateway:
    def __init__(self) -> None:
        self.session_titles: list[str] = []
        self.goal_payloads: list[tuple[str, str, list[str]]] = []
        self.messages: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    async def create_session(self, title: str) -> SessionRecord:
        self.session_titles.append(title)
        return SessionRecord(
            session_id="session-1",
            title=title,
            status="active",
            created_at="2026-07-18T00:00:00Z",
            updated_at="2026-07-18T00:00:00Z",
        )

    async def create_research_goal(self, session_id: str, objective: str, criteria: list[str]) -> GoalSnapshot:
        self.goal_payloads.append((session_id, objective, criteria))
        return GoalSnapshot(goal={"goal_id": "goal-1"})

    async def send_message(self, session_id: str, content: str) -> MessageAccepted:
        self.messages.append((session_id, content))
        return MessageAccepted(message_id="message-1", attempt_id="attempt-1")

    async def cancel(self, session_id: str) -> CancelResult:
        self.cancelled.append(session_id)
        return CancelResult(status="cancelled")


class FakeWatcher:
    def __init__(self, outcome: AttemptOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    async def wait(self, session_id: str, attempt_id: str) -> AttemptOutcome:
        self.calls.append((session_id, attempt_id))
        return self.outcome


def outcome_with(*events: SseEvent, status: AttemptStatus = AttemptStatus.COMPLETED) -> AttemptOutcome:
    terminal = next((event for event in events if event.event_type.startswith("attempt.")), None)
    return AttemptOutcome("session-1", "attempt-1", status, tuple(events), terminal, None, False)


async def test_probe_requires_observed_successful_tool_call_and_result() -> None:
    events = (
        SseEvent("e1", "tool_call", {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME}),
        SseEvent("e2", "tool_result", {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME, "status": "ok"}),
        SseEvent("e3", "attempt.completed", {"attempt_id": "attempt-1"}),
    )
    gateway = FakeGateway()
    watcher = FakeWatcher(outcome_with(*events))

    result = await PortfolioMcpProbe(gateway, watcher).run()

    assert result.status is McpStatus.AVAILABLE
    assert result.observed_tools == [EXPECTED_VIBE_TOOL_NAME]
    assert watcher.calls == [("session-1", "attempt-1")]
    assert "Do not place orders" in gateway.messages[0][1]
    assert "mcpServers" not in gateway.messages[0][1]


async def test_completed_run_without_tool_event_is_missing_not_success() -> None:
    gateway = FakeGateway()
    watcher = FakeWatcher(
        outcome_with(SseEvent("e1", "attempt.completed", {"attempt_id": "attempt-1"}))
    )

    result = await PortfolioMcpProbe(gateway, watcher).run()

    assert result.status is McpStatus.MISSING
    assert result.reason == "expected_tool_call_not_observed"


async def test_timed_out_probe_cancels_original_session() -> None:
    gateway = FakeGateway()
    watcher = FakeWatcher(outcome_with(status=AttemptStatus.TIMED_OUT))

    result = await PortfolioMcpProbe(gateway, watcher).run()

    assert result.status is McpStatus.FAILED
    assert result.reason == "probe_timed_out"
    assert gateway.cancelled == ["session-1"]
```

- [ ] **Step 2: Run probe tests and confirm the modules are absent**

Run: `uv run pytest tests/vibe/test_mcp_probe.py -q`

Expected: FAIL during collection because `vibe_portfolio.vibe.mcp_probe` does not exist.

- [ ] **Step 3: Implement the research-only coordinator**

Create `src/vibe_portfolio/vibe/research.py`:

```python
from dataclasses import dataclass
from typing import Protocol

from vibe_portfolio.vibe.models import GoalSnapshot, MessageAccepted, SessionRecord


class ResearchGateway(Protocol):
    async def create_session(self, title: str) -> SessionRecord:
        ...

    async def create_research_goal(self, session_id: str, objective: str, criteria: list[str]) -> GoalSnapshot:
        ...

    async def send_message(self, session_id: str, content: str) -> MessageAccepted:
        ...


@dataclass(frozen=True, slots=True)
class StartedResearch:
    session_id: str
    goal_id: str
    message_id: str
    attempt_id: str


class ResearchCoordinator:
    def __init__(self, gateway: ResearchGateway) -> None:
        self.gateway = gateway

    async def start(self, *, title: str, objective: str, criteria: list[str], message: str) -> StartedResearch:
        session = await self.gateway.create_session(title)
        goal = await self.gateway.create_research_goal(session.session_id, objective, criteria)
        accepted = await self.gateway.send_message(session.session_id, message)
        goal_id = str(goal.goal.get("goal_id", ""))
        if not goal_id:
            raise ValueError("Vibe research goal response did not contain goal_id")
        return StartedResearch(session.session_id, goal_id, accepted.message_id, accepted.attempt_id)
```

- [ ] **Step 4: Implement the event-proven MCP probe**

Create `src/vibe_portfolio/vibe/mcp_probe.py`:

```python
from dataclasses import dataclass
from typing import Protocol

from vibe_portfolio.compatibility import McpStatus
from vibe_portfolio.vibe.models import CancelResult
from vibe_portfolio.vibe.research import ResearchCoordinator, ResearchGateway
from vibe_portfolio.vibe.watcher import AttemptOutcome, AttemptStatus

EXPECTED_VIBE_TOOL_NAME = "mcp_portfolio_portfolio_get_capabilities"

PROBE_MESSAGE = """Run a read-only compatibility check.
Call exactly mcp_portfolio_portfolio_get_capabilities once and summarize its schema_version and read_only fields.
Do not place orders, do not call broker-write tools, do not execute trades, and do not modify portfolio data.
This is a protocol test, not investment advice."""


class ProbeGateway(ResearchGateway, Protocol):
    async def cancel(self, session_id: str) -> CancelResult:
        ...


class ProbeWatcher(Protocol):
    async def wait(self, session_id: str, attempt_id: str) -> AttemptOutcome:
        ...


@dataclass(frozen=True, slots=True)
class McpProbeResult:
    status: McpStatus
    session_id: str
    attempt_id: str
    observed_tools: list[str]
    reason: str | None = None


class PortfolioMcpProbe:
    def __init__(self, gateway: ProbeGateway, watcher: ProbeWatcher) -> None:
        self.gateway = gateway
        self.watcher = watcher

    async def run(self) -> McpProbeResult:
        started = await ResearchCoordinator(self.gateway).start(
            title="Portfolio MCP compatibility probe",
            objective="Verify the operator-approved read-only Portfolio MCP boundary",
            criteria=[
                "Observe the exact Portfolio MCP tool call",
                "Observe a successful tool result",
                "Perform no order placement or portfolio mutation",
            ],
            message=PROBE_MESSAGE,
        )
        outcome = await self.watcher.wait(started.session_id, started.attempt_id)
        observed_tools = [
            str(event.data.get("tool"))
            for event in outcome.events
            if event.event_type == "tool_call" and event.data.get("tool")
        ]
        successful = any(
            event.event_type == "tool_result"
            and event.data.get("tool") == EXPECTED_VIBE_TOOL_NAME
            and event.data.get("status") == "ok"
            for event in outcome.events
        )
        if EXPECTED_VIBE_TOOL_NAME in observed_tools and successful:
            return McpProbeResult(McpStatus.AVAILABLE, started.session_id, started.attempt_id, observed_tools)
        if outcome.status is AttemptStatus.TIMED_OUT:
            await self.gateway.cancel(started.session_id)
            return McpProbeResult(McpStatus.FAILED, started.session_id, started.attempt_id, observed_tools, "probe_timed_out")
        if EXPECTED_VIBE_TOOL_NAME not in observed_tools:
            return McpProbeResult(McpStatus.MISSING, started.session_id, started.attempt_id, observed_tools, "expected_tool_call_not_observed")
        return McpProbeResult(McpStatus.FAILED, started.session_id, started.attempt_id, observed_tools, "tool_result_not_successful")
```

- [ ] **Step 5: Run probe, watcher, and gateway regression tests**

Run: `uv run pytest tests/vibe/test_mcp_probe.py tests/vibe/test_watcher.py tests/vibe/test_gateway.py -q && uv run ruff check src tests && uv run mypy src`

Expected: `13 passed`; Ruff and mypy exit 0.

- [ ] **Step 6: Commit the explicit MCP proof flow**

```bash
git add src/vibe_portfolio/vibe/research.py src/vibe_portfolio/vibe/mcp_probe.py tests/vibe/test_mcp_probe.py
git commit -m "feat: verify portfolio mcp through vibe"
```

### Task 7: Sidecar System API and Machine-Readable Compatibility CLI

**Files:**
- Create: `src/vibe_portfolio/api/__init__.py`
- Create: `src/vibe_portfolio/api/app.py`
- Create: `src/vibe_portfolio/api/main.py`
- Create: `src/vibe_portfolio/cli/__init__.py`
- Create: `src/vibe_portfolio/cli/compatibility.py`
- Create: `tests/api/test_system_api.py`

**Interfaces:**
- Consumes: `CompatibilityDiscovery`, `PortfolioMcpProbe`, `AttemptWatcher`, `VibeGateway`, and `Settings`.
- Produces: `GET /api/v1/system/status`, `GET /api/v1/system/compatibility`, explicit `POST /api/v1/system/compatibility/mcp-probe`, `create_app()`, and `portfolio-compat-check --contract-only`.

- [ ] **Step 1: Write failing Sidecar API tests**

Create `tests/api/test_system_api.py`:

```python
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
    async def run(self) -> McpProbeResult:
        return McpProbeResult(McpStatus.AVAILABLE, "session-1", "attempt-1", ["mcp_portfolio_portfolio_get_capabilities"])


def test_get_compatibility_is_read_only_and_does_not_run_mcp_probe() -> None:
    discovery = FakeDiscovery()
    app = create_app(AppServices(discovery=discovery, mcp_probe=FakeProbe()))

    with TestClient(app) as client:
        response = client.get("/api/v1/system/compatibility")

    assert response.status_code == 200
    assert response.json()["state"] == "degraded"
    assert response.json()["mcp_status"] == "not_checked"
    assert discovery.statuses == [McpStatus.NOT_CHECKED]


def test_post_probe_is_explicit_and_updates_compatibility() -> None:
    discovery = FakeDiscovery()
    app = create_app(AppServices(discovery=discovery, mcp_probe=FakeProbe()))

    with TestClient(app) as client:
        probe = client.post("/api/v1/system/compatibility/mcp-probe")
        status = client.get("/api/v1/system/status")

    assert probe.status_code == 200
    assert probe.json()["probe"]["status"] == "available"
    assert probe.json()["compatibility"]["state"] == "compatible"
    assert status.json() == {"status": "ok", "service": "Vibe-Trading Portfolio", "mcp_status": "available"}
```

- [ ] **Step 2: Run API tests and confirm the application module is absent**

Run: `uv run pytest tests/api/test_system_api.py -q`

Expected: FAIL during collection because `vibe_portfolio.api.app` does not exist.

- [ ] **Step 3: Implement the Sidecar application factory and loopback entry point**

Create `src/vibe_portfolio/api/__init__.py`:

```python
"""Portfolio Sidecar HTTP API."""
```

Create `src/vibe_portfolio/api/app.py`:

```python
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from fastapi import FastAPI, Request

from vibe_portfolio.compatibility import CompatibilityDiscovery, CompatibilityReport, McpStatus
from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.gateway import VibeGateway
from vibe_portfolio.vibe.mcp_probe import McpProbeResult, PortfolioMcpProbe
from vibe_portfolio.vibe.watcher import AttemptWatcher


class DiscoveryPort(Protocol):
    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
        ...


class McpProbePort(Protocol):
    async def run(self) -> McpProbeResult:
        ...


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
```

Create `src/vibe_portfolio/api/main.py`:

```python
import uvicorn

from vibe_portfolio.api.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement the contract CLI with failure exit codes**

Create `src/vibe_portfolio/cli/__init__.py`:

```python
"""Portfolio operator commands."""
```

Create `src/vibe_portfolio/cli/compatibility.py`:

```python
import argparse
import asyncio

from vibe_portfolio.compatibility import CompatibilityDiscovery, McpStatus
from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.gateway import VibeGateway


async def _check(contract_only: bool) -> int:
    gateway = VibeGateway(Settings())
    try:
        report = await CompatibilityDiscovery(gateway).discover(McpStatus.NOT_CHECKED)
    finally:
        await gateway.close()
    print(report.model_dump_json(indent=2))
    if contract_only:
        return 0 if report.contract_compatible else 2
    return 0 if report.deep_analysis_enabled else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Vibe-Trading compatibility")
    parser.add_argument(
        "--contract-only",
        action="store_true",
        help="Require version and routes but allow provider readiness and MCP verification to remain degraded",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_check(args.contract_only)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run API, regression, and static checks**

Run: `uv run pytest tests/api/test_system_api.py tests/vibe tests/mcp -q && uv run ruff check src tests && uv run mypy src`

Expected: all selected tests pass; Ruff and mypy exit 0.

- [ ] **Step 6: Smoke-test the CLI against an offline Vibe URL**

Run: `PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:9 uv run portfolio-compat-check --contract-only`

Expected: exit code 2 and JSON containing `"state": "degraded"`, `"analysis_mode": "disabled"`, and `"contract_compatible": false`.

- [ ] **Step 7: Commit the system surface**

```bash
git add src/vibe_portfolio/api src/vibe_portfolio/cli tests/api/test_system_api.py
git commit -m "feat: expose compatibility diagnostics"
```

### Task 8: Live Contract Harness, Upstream Matrix, Runbook, and Release Verification

**Files:**
- Create: `compatibility/baseline.json`
- Create: `tests/contract/test_live_vibe_contract.py`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/upstream-compatibility.yml`
- Modify: `README.md`
- Create: `docs/runbooks/vibe-compatibility.md`

**Interfaces:**
- Consumes: `portfolio-compat-check`, Sidecar API/MCP entry points, `CompatibilityDiscovery`, and the pinned upstream public contract.
- Produces: reproducible local verification, opt-in live tests, minimum/stable/latest scheduled checks, and the operator installation/recovery procedure.

- [ ] **Step 1: Add the pinned compatibility baseline**

Create `compatibility/baseline.json`:

```json
{
  "schema_version": "vibe-compatibility-baseline.v1",
  "captured_at": "2026-07-18",
  "repository": "https://github.com/zhaibin/Vibe-Trading.git",
  "minimum": {
    "ref": "67a393e4574865e8ab9b1b3f9a9fd1d7ab337343",
    "version": "0.1.11"
  },
  "stable": {
    "ref": "67a393e4574865e8ab9b1b3f9a9fd1d7ab337343",
    "version": "0.1.11"
  },
  "latest": {
    "ref": "main",
    "allowed_to_advance_support_range": false
  },
  "required_endpoints": [
    ["POST", "/sessions"],
    ["POST", "/sessions/{session_id}/goal"],
    ["POST", "/sessions/{session_id}/messages"],
    ["GET", "/sessions/{session_id}/messages"],
    ["GET", "/sessions/{session_id}/events"],
    ["POST", "/auth/sse-ticket"],
    ["POST", "/sessions/{session_id}/cancel"]
  ]
}
```

- [ ] **Step 2: Write the opt-in live contract test**

Create `tests/contract/test_live_vibe_contract.py`:

```python
import os

import pytest

from vibe_portfolio.compatibility import CompatibilityDiscovery, McpStatus
from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.gateway import VibeGateway


@pytest.mark.contract
async def test_running_vibe_matches_supported_public_contract() -> None:
    base_url = os.environ.get("PORTFOLIO_VIBE_BASE_URL")
    if not base_url:
        pytest.skip("PORTFOLIO_VIBE_BASE_URL is not set")
    gateway = VibeGateway(Settings())
    try:
        report = await CompatibilityDiscovery(gateway).discover(McpStatus.NOT_CHECKED)
    finally:
        await gateway.close()

    assert report.contract_compatible, report.model_dump_json(indent=2)
    assert report.vibe_version is not None
    assert report.missing_capabilities == []
```

- [ ] **Step 3: Add hermetic CI**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.11"
          enable-cache: true
      - run: uv sync --frozen --extra dev
      - run: uv run ruff check src tests
      - run: uv run mypy src
      - run: uv run pytest -m "not contract" --cov=vibe_portfolio --cov-report=term-missing
```

- [ ] **Step 4: Add minimum, stable, and latest upstream contract checks**

Create `.github/workflows/upstream-compatibility.yml`:

```yaml
name: upstream compatibility

on:
  workflow_dispatch:
  schedule:
    - cron: "17 2 * * *"

permissions:
  contents: read

jobs:
  contract:
    strategy:
      fail-fast: false
      matrix:
        baseline:
          - name: minimum
            ref: 67a393e4574865e8ab9b1b3f9a9fd1d7ab337343
          - name: stable
            ref: 67a393e4574865e8ab9b1b3f9a9fd1d7ab337343
          - name: latest
            ref: main
    runs-on: ubuntu-latest
    steps:
      - name: Check out Portfolio sidecar
        uses: actions/checkout@v4
      - name: Check out Vibe-Trading ${{ matrix.baseline.name }}
        uses: actions/checkout@v4
        with:
          repository: zhaibin/Vibe-Trading
          ref: ${{ matrix.baseline.ref }}
          path: upstream
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.11"
          enable-cache: true
      - name: Install both repositories
        run: |
          uv sync --frozen --extra dev
          uv venv upstream/.venv --python 3.11
          uv pip install --python upstream/.venv/bin/python -e ./upstream
      - name: Start Vibe-Trading without an LLM credential
        env:
          API_AUTH_KEY: compatibility-test-key
        run: |
          upstream/.venv/bin/vibe-trading serve --host 127.0.0.1 --port 8899 > /tmp/vibe-trading.log 2>&1 &
          for attempt in $(seq 1 60); do
            if curl --fail --silent http://127.0.0.1:8899/live > /dev/null; then
              exit 0
            fi
            sleep 1
          done
          cat /tmp/vibe-trading.log
          exit 1
      - name: Verify version and required public routes
        env:
          PORTFOLIO_VIBE_BASE_URL: http://127.0.0.1:8899
          PORTFOLIO_VIBE_API_KEY: compatibility-test-key
        run: uv run portfolio-compat-check --contract-only
      - name: Upload Vibe log on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: vibe-${{ matrix.baseline.name }}-log
          path: /tmp/vibe-trading.log
```

- [ ] **Step 5: Add the operator README**

Create `README.md`:

```markdown
# Vibe-Trading Portfolio

Independent personal-portfolio Sidecar for [zhaibin/Vibe-Trading](https://github.com/zhaibin/Vibe-Trading). The Sidecar never modifies or imports Vibe-Trading source code.

Milestone 0 proves only the external integration boundary: Vibe version/capability discovery, research-only Sessions, SSE recovery with polling fallback, cancellation, and one operator-approved read-only MCP tool. Ledger, imports, analytics, recommendations, scheduling, and UI arrive in later milestones.

## Local setup

```bash
uv sync --extra dev
cp .env.example .env
uv run portfolio-generate-vibe-config --output-dir var/install
```

Review `var/install/vibe-portfolio-mcp-snippet.json` and manually merge its `mcpServers.portfolio` object into `~/.vibe-trading/agent.json`. Do not set `ALLOW_SESSION_MCP_SERVERS=1`.

Start the MCP server and Sidecar API in separate terminals:

```bash
PORTFOLIO_MCP_TOKEN_FILE=var/install/mcp-token uv run portfolio-mcp
uv run portfolio-api
```

Check the public Vibe contract without spending model budget:

```bash
uv run portfolio-compat-check --contract-only
```

Run the explicit MCP probe only after Vibe-Trading is ready and the operator snippet is installed:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/system/compatibility/mcp-probe
```

The probe creates one research-only Vibe Session and may consume model budget. A successful result must contain observed `tool_call` and `tool_result` events for `mcp_portfolio_portfolio_get_capabilities`.

## Development

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -m "not contract"
```

See [the compatibility runbook](docs/runbooks/vibe-compatibility.md) for states, upgrades, token rotation, and failure recovery.
```

- [ ] **Step 6: Add the compatibility and recovery runbook**

Create `docs/runbooks/vibe-compatibility.md`:

```markdown
# Vibe Compatibility Runbook

## Trust boundary

Portfolio and Vibe-Trading are separate repositories and processes. Portfolio sends no `mcpServers` Session override and never enables `ALLOW_SESSION_MCP_SERVERS`. The operator manually installs a generated `streamableHttp` MCP snippet with a single explicit read-only tool.

## State meanings

| State | Analysis mode | Meaning | Recovery |
|---|---|---|---|
| `compatible` | `full_mcp` | Supported version/routes, ready provider, verified MCP events | No action |
| `degraded` | `bounded_context` | Supported/ready Vibe, MCP missing or unverified | Start Portfolio MCP, merge the snippet, restart Vibe, run explicit probe |
| `degraded` | `disabled` | Vibe is offline, unauthenticated, or not ready | Start Vibe, correct `PORTFOLIO_VIBE_API_KEY`, or configure its LLM provider |
| `unsupported` | `disabled` | Version is outside `>=0.1.11,<0.2.0` or a required route is missing | Keep local Portfolio functions available; add and test a new gateway adapter before widening support |

## First installation

1. Run `uv run portfolio-generate-vibe-config --output-dir var/install`.
2. Confirm both generated files are mode `0600`.
3. Review `var/install/vibe-portfolio-mcp-snippet.json`.
4. Manually merge only `mcpServers.portfolio` into `~/.vibe-trading/agent.json`.
5. Start MCP with `PORTFOLIO_MCP_TOKEN_FILE=var/install/mcp-token uv run portfolio-mcp`.
6. Restart Vibe-Trading so it reloads operator configuration.
7. Run `uv run portfolio-compat-check --contract-only`.
8. Run `POST /api/v1/system/compatibility/mcp-probe` once and confirm `available`.

## Upgrade check

1. Pull Vibe-Trading in its own repository; do not copy Portfolio files into it.
2. Start the upgraded Vibe instance.
3. Run `uv run portfolio-compat-check --contract-only` before any deep analysis.
4. If `unsupported`, do not widen `SUPPORTED_VIBE_VERSIONS` until route fixtures, gateway tests, and the latest matrix pass.
5. If the contract passes, run the explicit MCP probe and inspect observed tool events.

## SSE recovery

The Sidecar reconnects to the original Session with its last event ID and a newly minted one-shot ticket. It never creates a replacement Session during recovery. After two failed reconnects it polls messages for the original `attempt_id`. Timeout cancellation targets the original `session_id`.

## Token rotation

1. Stop Portfolio MCP.
2. Archive `var/install` outside the repository or delete it explicitly.
3. Generate a new bundle.
4. Manually replace the Portfolio bearer header in Vibe operator configuration.
5. Start Portfolio MCP with the new token file and restart Vibe.
6. Run the explicit MCP probe.

Never commit the token or snippet. Never log their contents.
```

- [ ] **Step 7: Run the complete local release gate**

Run:

```bash
uv lock --check
uv run ruff check src tests
uv run mypy src
uv run pytest -m "not contract" --cov=vibe_portfolio --cov-report=term-missing
git status --short
```

Expected: lock check, Ruff, mypy, and tests exit 0; `git status --short` lists only the Task 8 files before commit.

- [ ] **Step 8: Confirm upstream isolation**

Run from the Portfolio repository, substituting only the already known sibling path:

```bash
git -C /Users/zhaibin/Dev/AInvest status --short
git -C /Users/zhaibin/Dev/AInvest diff --exit-code
```

Expected: no tracked upstream source diff. Existing local CodeGraph metadata may appear as untracked and must not be added to either product commit.

- [ ] **Step 9: Run the live baseline contract when Vibe is available**

Run:

```bash
PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:8899 \
PORTFOLIO_VIBE_API_KEY="$VIBE_COMPAT_API_KEY" \
uv run pytest tests/contract/test_live_vibe_contract.py -q
```

Expected: `1 passed`. If Vibe is intentionally offline, record this opt-in gate as not run; do not reinterpret offline as a contract pass.

- [ ] **Step 10: Commit the contract harness and documentation**

```bash
git add compatibility/baseline.json tests/contract/test_live_vibe_contract.py .github/workflows/ci.yml .github/workflows/upstream-compatibility.yml README.md docs/runbooks/vibe-compatibility.md
git commit -m "test: add vibe compatibility release gate"
```

## Final Verification Checklist

- [ ] `uv run pytest -m "not contract"` passes without Vibe, network, or model credentials.
- [ ] `uv run ruff check src tests` passes.
- [ ] `uv run mypy src` passes in strict mode.
- [ ] Offline compatibility is `degraded/disabled`, not a Sidecar startup failure.
- [ ] Supported but unverified MCP compatibility is `degraded/bounded_context`.
- [ ] Version `0.2.0` and a missing cancel route are `unsupported/disabled`.
- [ ] A Session request contains no MCP definition and the goal is always `research_general`.
- [ ] SSE reconnect uses the same `session_id`, last event ID, and a new one-shot ticket.
- [ ] Polling and cancellation target the original `attempt_id` and `session_id`.
- [ ] MCP lists one tool with read-only, non-destructive, idempotent, closed-world annotations.
- [ ] Generated Vibe config uses `streamableHttp`, loopback, bearer auth, and an explicit one-tool allowlist.
- [ ] The MCP probe requires observed Vibe `tool_call` plus successful `tool_result`; assistant prose alone cannot pass it.
- [ ] Minimum, stable, and latest upstream jobs do not silently widen the support range.
- [ ] No Vibe-Trading tracked source file changed.

## Deferred to Later Milestone Plans

- MVP: ledger, accounts, instruments, imports, reconciliation, prices/FX, snapshots, risk profile, deterministic diagnostics, bounded portfolio context, one deep-analysis report, backup/restore, and UI.
- v1.1: per-position research, daily/weekly briefs, report history, thesis tracking, and cost/latency reporting.
- v1.2: constrained optimizer, current-versus-candidate backtests, Trade Journal/Shadow Account adapters, Swarm committee, and read-only broker sync.
