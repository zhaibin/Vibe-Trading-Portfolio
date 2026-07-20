# Current Project Handoff

**Last updated:** 2026-07-20
**Project:** Vibe-Trading Portfolio sidecar
**Current milestone:** Experience Milestone 1A release candidate implemented and hermetically verified; Task 16 review/commit/integration remains approval-gated

## Start here

This is dated evidence, not a substitute for live inspection. Follow the startup protocol in [`AGENTS.md`](../../AGENTS.md), compare Git state, and obtain explicit approval before modifying repository files or starting another milestone.

## Repository state

- Active branch: `codex/portfolio-experience-webui`
- Verified HEAD before the uncommitted Task 16 release work: `6cf6b09e5c4e426dd600d32b75370dc9be064d90` (`test: cover portfolio experience end to end`)
- Latest milestone commits: `518f191` overview/settings, `263c5f9` and `14dbf29` holdings hardening, `2460d87` holdings UI, `582e43` typed-shell hardening, `de28a42` typed web shell, `56d8808` and `7edf17f` secure-app hardening, `79d507b` secure same-origin app
- Task 16 state: uncommitted by explicit instruction; inspect `git status --short` for the complete review set
- Remote: `origin` uses SSH
- Remote `main` at last query: `c65c0045dc8ff7c75bdacd028581caf0591f905e`
- Current feature HEAD was 42 commits ahead and 0 behind `origin/main`; the feature branch has no configured upstream and has not been pushed by this task
- Local `main` was at `46f2cf8` and reported four commits ahead of `origin/main`
- No force-push or remote mutation was performed

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
- `uv run pytest -m "not contract and not market_contract" --cov=vibe_portfolio --cov-report=term-missing --cov-fail-under=85`: 610 passed, 4 deselected, 87.05% coverage.
- `uv run python scripts/export_openapi.py`: passed.
- `npm ci --prefix frontend`: passed after managed-cache approval; 381 packages installed, 0 vulnerabilities.
- `npm --prefix frontend run api:types`: passed; generated OpenAPI/type diff was empty.
- `npm --prefix frontend run check`: passed; 8 files and 72 tests, 89.56% line coverage, production build successful.
- `npm --prefix frontend run e2e`: phase 1 passed in 2.9 seconds and restart phase 2 passed in 951 milliseconds.
- `uv build --sdist`, followed by `uv build --wheel <sdist>`: built a 156-member curated source archive and a 58-member wheel from that archive.
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

Review the uncommitted Task 16 probe, CI/package metadata, documentation, boundary scans, and this evidence. With explicit approval, create the final milestone commit, integrate the reviewed 16-task branch, rerun the merged-result release gate, and publish through normal fast-forward semantics. Do not begin the formal ledger milestone implicitly.
