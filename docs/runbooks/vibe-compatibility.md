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
