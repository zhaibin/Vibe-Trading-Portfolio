# Current Project Handoff

**Last updated:** 2026-07-20
**Project:** Vibe-Trading Portfolio sidecar
**Current milestone:** Experience Milestone 1A implemented, independently reviewed, and hermetically verified; remote publication is the next action

## Start here

This is dated evidence, not a substitute for live inspection. Follow the startup protocol in [`AGENTS.md`](../../AGENTS.md), compare Git state, and obtain explicit approval before modifying repository files or starting another milestone.

## Repository state

- Active branch: `codex/portfolio-experience-webui`
- Verified implementation HEAD before this handoff refresh: `5bcb562f8ddc990c8c5d3af3ebf39b229f3a8bd1` (`fix: build wheel from sanitized source archive`)
- Latest milestone commits: `5bcb562` sanitized sdist-to-wheel fix, `eba1548` release/CI/docs, `6cf6b09` production E2E, `518f191` overview/settings, `263c5f9` and `14dbf29` holdings hardening, `2460d87` holdings UI
- All 16 planned tasks and the final packaging correction are committed; whole-branch review found no remaining P1/P2 issues
- Remote: `origin` uses SSH
- Remote `main` at last query: `c65c0045dc8ff7c75bdacd028581caf0591f905e`
- Before this handoff-only commit, the feature branch was 44 commits ahead and 0 behind `origin/main`; it had no configured upstream and had not yet been pushed
- Local `main` was at `46f2cf8` and reported four commits ahead of `origin/main`
- No force-push was performed; publish the named feature branch without rewriting history

## Implemented Experience Milestone 1A scope

- Owner-private SQLite storage with versioned migrations, integrity/path checks, bounded busy behavior, pre-migration backups, and retention.
- Exact accounts, confirmed instruments, current positions, archive/restore, idempotency, optimistic concurrency, pagination, and sanitized versioned APIs.
- Currency-local CNY/HKD/USD valuation with unknown cash, fresh/stale/unavailable quote states, preserved last-valid quotes, and no cross-currency total.
- Sidecar-owned reviewed Eastmoney, Yahoo, and Tencent adapters with destination allowlists, response/time/concurrency bounds, search confirmation, route fallback, and no Vibe dependency.
- Same-origin loopback FastAPI/React WebUI with overview, holdings, settings/status, explicit refresh, keyboard behavior, responsive tables, accessibility checks, and JSON API 404 precedence over SPA fallback.
- Production-build Playwright coverage using deterministic injected providers, a protected temporary database, process restart/persistence, concurrency conflict recovery, privacy scanning, and signal cleanup.
- Opt-in public-provider probe limited to `510300.SH`, `00700.HK`, and `AAPL.US`, independently reporting and validating each reviewed provider route.
- Locked CI frontend/E2E/release workflow with a curated source distribution, wheel-from-sdist build, archive privacy checks, and wheel inclusion of built SPA assets.

## Hard boundaries

- The sidecar remains independent of Vibe internals, databases, private files, and runtime patches. It integrates only through public REST/OpenAPI/SSE and operator-installed read-only MCP.
- Never set `ALLOW_SESSION_MCP_SERVERS=1` or send Session `mcpServers` overrides.
- MCP remains loopback-only, bearer-authenticated, read-only, and explicitly allowlisted.
- There is no broker write, order placement, trade execution, transaction reconstruction, or instruction implying those capabilities.
- The WebUI has no login and must remain loopback-only. Do not expose it through a public bind, proxy, or tunnel.
- Tokens, keys, account identifiers, holdings, balances, and database contents must stay out of Git and handoff documents.

## Fresh hermetic release evidence

The complete Task 16 gate was run on 2026-07-20:

- `uv sync --frozen --extra dev`: passed after managed-cache approval.
- `uv lock --check`: passed; 111 packages resolved.
- `uv run ruff check src tests migrations scripts`: passed.
- `uv run mypy src`: passed; 48 source files clean under strict mypy.
- Latest full hermetic pytest gate: 649 passed, 4 deselected, 87.05% coverage, including all 39 packaging tests and real direct/generated-sdist Hatch builds.
- `uv run python scripts/export_openapi.py`: passed.
- `npm ci --prefix frontend`: passed after managed-cache approval; 381 packages installed, 0 vulnerabilities.
- `npm --prefix frontend run api:types`: passed; generated OpenAPI/type diff was empty.
- `npm --prefix frontend run check`: passed; 8 files and 72 tests, 89.56% line coverage, production build successful.
- `npm --prefix frontend run e2e`: phase 1 passed in 2.7 seconds and restart phase 2 passed in 784 milliseconds.
- `uv build --sdist`, followed by `uv build --wheel <sdist>`: built a 156-member curated source archive and a 58-member wheel from that archive.
- A real uv/Hatch integration regression removes any copied ignored SPA output, synthesizes minimal production-shaped index/hashed CSS/JS assets, and uses a test-owned temporary uv cache to build the wheel directly from a VCS-free source copy and from its generated sdist. Both wheels pass the production artifact verifier and contain exactly one SPA index.
- The release verifier requires the exact Python/frontend build manifests and source, migration, script, runbook, test, frontend source, and E2E trees; it rejects local/generated artifacts, unsafe or duplicate-normalized archive members, ZIP links/special modes, personal home paths, and common concrete secret/token forms. The sanitized artifacts had zero personal home-path matches.
- Separate wheel assertions found exactly one `vibe_portfolio/web/dist/index.html`, one hashed CSS asset, and one hashed JavaScript asset.

The first non-escalated uv and npm-cache attempts failed at managed cache permissions before their payloads ran. Approved retries above are the actual successful gate evidence. Two dependency deprecation warnings remain upstream (`httpx`/Starlette TestClient and Authlib jose); they did not fail the suite.

## Opt-in external gate status

- Public market-provider gate: **not run**. Default test invocation reported one skip with `PORTFOLIO_RUN_MARKET_CONTRACT=1 is not set; market contract not run`.
- Vibe route contract: **not run**.
- Vibe runtime contract: **not run**.
- Operator MCP probe: **not run**.

No opt-in flag was set by Task 16. Skipped/not configured layers are not represented as passed. Follow [`docs/runbooks/vibe-compatibility.md`](../runbooks/vibe-compatibility.md) and [`docs/runbooks/portfolio-data.md`](../runbooks/portfolio-data.md) before explicitly enabling an external gate.

## Remaining scope and risks

Experience Milestone 1A remains a staged current-position snapshot, not the umbrella design's immutable-ledger MVP. Formal ledger events, transaction history, CSV import, realized performance, FX consolidation, research automation, restore UI, permanent deletion, remote authentication, scheduling, alerts, broker connectivity, and trading remain deliberately out of scope.

Public quote endpoints remain replaceable external dependencies with availability, response-shape, rate-limit, and usage-condition risk. A passed hermetic suite does not prove current live-provider availability.

## Authoritative references

- [Experience design](../superpowers/specs/2026-07-19-portfolio-experience-webui-design.md)
- [Experience implementation plan](../superpowers/plans/2026-07-19-portfolio-experience-webui.md)
- [Portfolio data runbook](../runbooks/portfolio-data.md)
- [Vibe compatibility runbook](../runbooks/vibe-compatibility.md)
- [Pinned upstream contract](../../compatibility/baseline.json)

## Recommended next action

Push `codex/portfolio-experience-webui` to `origin` without rewriting history, then use the normal review/merge workflow. Keep this worktree for any remote-review follow-up. Do not begin the formal ledger milestone implicitly.
