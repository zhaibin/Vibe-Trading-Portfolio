# Vibe-Trading Portfolio

Independent personal-portfolio Sidecar for [zhaibin/Vibe-Trading](https://github.com/zhaibin/Vibe-Trading). The Sidecar never modifies or imports Vibe-Trading source code.

Milestone 0 proves only the external integration boundary: Vibe version/capability discovery, research-only Sessions, SSE recovery with polling fallback, cancellation, and one operator-approved read-only MCP tool. Ledger, imports, analytics, recommendations, scheduling, and UI arrive in later milestones.

## New session handoff

Agents and maintainers should start with [`AGENTS.md`](AGENTS.md) and the current [`docs/handoff/CURRENT.md`](docs/handoff/CURRENT.md). A new session must verify the live Git state, report its understanding and recommended next step, and wait for user approval before continuing milestone work.

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

This is the route-only layer. A zero exit code does not prove provider readiness, runtime DTOs, SSE replay, or MCP.
With an isolated, ready Vibe test instance, explicitly run the public runtime layer:

```bash
PORTFOLIO_RUN_RUNTIME_CONTRACT=1 \
PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:8899 \
PORTFOLIO_VIBE_API_KEY="$VIBE_COMPAT_API_KEY" \
uv run pytest \
  tests/contract/test_live_vibe_contract.py::test_running_vibe_passes_the_public_runtime_contract \
  -q
```

The runtime gate creates one research-only Session, goal, and message; validates health, readiness, DTOs, a one-shot
SSE ticket, first-event replay, polling, and cancellation; and performs no order or broker write. It can contact the
configured test provider while the attempt starts, so use an isolated keyless/local provider when model spend is not
intended. If `PORTFOLIO_RUN_RUNTIME_CONTRACT=1` is omitted, pytest reports this layer as skipped/not run—not passed.

Run the explicit MCP probe only after Vibe-Trading is ready and the operator snippet is installed:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/system/compatibility/mcp-probe
```

The probe creates one research-only Vibe Session and may consume model budget. A successful result must contain observed `tool_call` and `tool_result` events for `mcp_portfolio_portfolio_get_capabilities`.

The repeatable full-MCP pytest gate is independently opt-in:

```bash
PORTFOLIO_RUN_MCP_PROBE=1 \
PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:8899 \
PORTFOLIO_VIBE_API_KEY="$VIBE_COMPAT_API_KEY" \
uv run pytest \
  tests/contract/test_live_vibe_contract.py::test_operator_configured_portfolio_mcp_probe \
  -q
```

Only run it after the operator MCP snippet and a model credential are installed. Without
`PORTFOLIO_RUN_MCP_PROBE=1`, the result is explicitly skipped/not run. Once enabled, missing, duplicate, reordered,
wrong-name, or unsuccessful tool events fail the test.

## Development

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -m "not contract" --cov=vibe_portfolio --cov-report=term-missing --cov-fail-under=85
```

The release policy requires at least 85% line coverage; falling below the threshold is a non-zero gate failure.

See [the compatibility runbook](docs/runbooks/vibe-compatibility.md) for states, upgrades, token rotation, and failure recovery.
