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
8. Run the explicit runtime pytest command below against an isolated ready Vibe instance.
9. Run `POST /api/v1/system/compatibility/mcp-probe` once and confirm `available`.

## Upgrade check

1. Pull Vibe-Trading in its own repository; do not copy Portfolio files into it.
2. Start the upgraded Vibe instance.
3. Run `uv run portfolio-compat-check --contract-only` before any deep analysis.
4. If `unsupported`, do not widen `SUPPORTED_VIBE_VERSIONS` until route fixtures, runtime tests, and the latest matrix pass.
5. Run the runtime gate; route-only success is insufficient for an upgrade.
6. If the runtime gate passes, run the explicit MCP probe and inspect observed tool events.

## Layered release gates

Route-only discovery is network-cheap and checks the supported version and OpenAPI methods:

```bash
uv run portfolio-compat-check --contract-only
```

Runtime verification requires `/health` and `/ready` success, then exercises Session creation, a `research_general`
goal, bounded safety instructions, message/ticket DTOs, SSE first-event and `Last-Event-ID` replay, polling, and
same-Session cancellation retry across the active-loop registration race:

```bash
PORTFOLIO_RUN_RUNTIME_CONTRACT=1 \
PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:8899 \
PORTFOLIO_VIBE_API_KEY="$VIBE_COMPAT_API_KEY" \
uv run pytest \
  tests/contract/test_live_vibe_contract.py::test_running_vibe_passes_the_public_runtime_contract \
  -q
```

Use an isolated keyless/local provider if provider spend is not intended. A ready `503`, malformed DTO, ticket/SSE
failure, replay drift, polling drift, or cancel drift makes this gate fail.

Full MCP verification is separate because it requires operator installation and may consume model budget:

```bash
PORTFOLIO_RUN_MCP_PROBE=1 \
PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:8899 \
PORTFOLIO_VIBE_API_KEY="$VIBE_COMPAT_API_KEY" \
uv run pytest \
  tests/contract/test_live_vibe_contract.py::test_operator_configured_portfolio_mcp_probe \
  -q
```

Omitting either opt-in flag means that layer is **skipped/not run**, never passed. Once enabled, any exception is a
test failure. MCP requires exactly one `mcp_portfolio_portfolio_get_capabilities` call followed by exactly one
successful result for the same attempt; assistant prose cannot satisfy it. The only additional tool events accepted
are ordered, correlated, successful pairs for `get_research_goal`, `add_goal_evidence`, and
`update_research_goal_status`. Any other, orphaned, unsuccessful, duplicate target, broker, execution, write, or
unknown tool event fails closed.

The hermetic release suite enforces at least 85% line coverage:

```bash
uv run pytest -m "not contract and not market_contract" \
  --cov=vibe_portfolio --cov-report=term-missing --cov-fail-under=85
```

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
