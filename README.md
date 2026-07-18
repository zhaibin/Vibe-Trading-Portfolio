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
