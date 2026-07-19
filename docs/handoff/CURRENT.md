# Current Project Handoff

**Last updated:** 2026-07-19
**Project:** Vibe-Trading Portfolio sidecar
**Current milestone:** Milestone 0 compatibility foundation completed; Experience Milestone 1A WebUI/independent-market-data design approved and audited; implementation planning not started

## Start here

This file is last-known evidence, not a substitute for inspecting the repository. A new session must first follow the startup protocol in [`AGENTS.md`](../../AGENTS.md), compare this handoff with live Git state, and report its understanding and proposed next step. Before modifying any repository file or starting milestone work, wait for explicit user approval.

## Last-known repository state

The following state was verified after local stabilization testing on 2026-07-19. It remains last-known evidence rather than a substitute for inspecting the live checkout:

- Target/integration branch: `main`
- Last verified active branch: `main`
- Local stabilization integration: `main` fast-forwarded from `3f3e081` to `96aeb56` after the merged-result gate passed
- Last verified published head: `c65c004` (`docs: record successful remote publication`)
- Approved Experience Milestone 1A design: `ee60736` (`docs: design portfolio experience webui`); this local design commit has not yet been pushed
- Live stabilization commits: `c15c12f` (`fix: poll for runtime terminal proof`), `7a26e3a` (`fix: validate goal-aware MCP probe events`), and `ef6938c` (`fix: retry runtime cancel registration race`)
- Live stabilization design/plan commits: `e1316f7`, `1d207b9`, and `3f3e081`
- Integrated handoff branch: `docs/new-session-handoff`
- Handoff branch base: `fb1e81e` (`docs: plan new-session handoff`)
- Handoff implementation commits: `0bbe561` (`docs: add new-session handoff`) and `16446c3` (`docs: clarify handoff approval gate`)
- Milestone 0 implementation head: `3b8e502` (`fix: reject pre-open token path replacement`)
- Handoff design commit: `9d4dfd5` (`docs: design new-session handoff`)
- Plan commit: `fb1e81e` (`docs: plan new-session handoff`)
- Remote: `origin` is `git@github.com:zhaibin/Vibe-Trading-Portfolio.git`; local `main` tracks `origin/main`
- Remote reconciliation: the unrelated `origin/main` initial history was preserved by merging `cab3ffb` (`Initial commit`) as the second parent of `6b1ee52`; its `LICENSE` is now tracked locally. No force-push was used.
- Remote publication: HTTPS push was rejected because the OAuth token lacked GitHub's `workflow` scope; the already-authenticated SSH transport then advanced remote `main` normally from `cab3ffb` to `6ecf516` without widening token permissions.
- Upstream Vibe baseline: `0.1.11` at `67a393e4574865e8ab9b1b3f9a9fd1d7ab337343`
- Supported Vibe range: `>=0.1.11,<0.2.0`

Always rerun `git status --short --branch`, `git rev-parse HEAD`, `git log --oneline -10`, and `git remote -v`. Report differences before continuing.

## Project objective

Build a personal-holdings module that can use Vibe-Trading's public analysis capabilities without modifying or coupling to Vibe-Trading internals. The sidecar must remain independently upgradeable and fail closed when the external contract cannot be verified.

## Verified completed scope

Milestone 0 established the external integration boundary:

- typed public REST gateway and DTO validation;
- version, readiness, route, and OpenAPI compatibility negotiation;
- bounded SSE reconnect/replay with original Session and Attempt polling fallback;
- authenticated loopback-only read-only Portfolio MCP with manual operator installation;
- research-only Session coordination with a 4,000-character message ceiling and explicit no-trading instructions;
- evidence-based MCP probe requiring the exact tool call and successful result;
- bounded same-Session cancellation retry for Vibe's `attempt.started`/active-loop registration race;
- strict MCP event validation that permits only correlated successful `get_research_goal`, `add_goal_evidence`, and `update_research_goal_status` control-plane pairs in addition to the one exact Portfolio capability pair;
- diagnostic system API and machine-readable compatibility CLI;
- pinned compatibility baseline, minimum/stable/latest CI matrix, layered route/runtime/MCP gates, and an 85% coverage threshold.

The latest hermetic verification on `codex/live-contract-stabilization` reported 139 passed, 3 deselected, 90.42% coverage, clean Ruff, strict mypy, and lock checks. The route-only check reported Vibe `0.1.11`, a compatible contract, and no missing capabilities. The explicit live runtime gate passed in 0.81 seconds, and the explicit operator-configured MCP gate passed in 32.77 seconds. Treat these results as dated evidence and rerun the gates before relying on them after any change.

## Explicitly not implemented

- holdings ledger and transaction model;
- CSV, broker, or manual holdings import;
- valuation, cost basis, performance, exposure, concentration, or risk analytics;
- portfolio-aware Vibe context assembly beyond the compatibility probe;
- recommendation workflows, scheduling, alerts, or UI;
- broker connectivity, order placement, trade execution, or any write action.

Do not describe the project as having a usable personal portfolio module yet.

The approved next milestone is now specified, but none of its runtime capability is implemented. In particular, the repository still has no React WebUI, accounts/positions database, independent instrument search, quote refresh, valuation summary, frontend test suite, or portfolio migrations.

## Hard boundaries

- `/Users/zhaibin/Dev/AInvest` is the separate upstream Vibe checkout and must not be modified by sidecar work.
- Use only public REST, OpenAPI, SSE, and operator-installed MCP.
- Never use Vibe internal imports, shared private storage, runtime patches, Session `mcpServers`, or `ALLOW_SESSION_MCP_SERVERS=1`.
- Keep MCP on `127.0.0.1`, bearer-authenticated, owner-secret protected, read-only, and explicitly allowlisted.
- Never persist secrets or personal holdings in this handoff.
- Unknown versions, missing or malformed routes, readiness failures, incomplete terminal states, and unverifiable MCP events remain fail-closed.
- Analysis prompts must continue to prohibit order placement, broker writes, and trade execution.

## Authoritative references

- [Product design](../superpowers/specs/2026-07-18-personal-portfolio-sidecar-design.md)
- [Experience Milestone 1A WebUI and independent market data design](../superpowers/specs/2026-07-19-portfolio-experience-webui-design.md)
- [Milestone 0 implementation plan](../superpowers/plans/2026-07-18-vibe-compatibility-spike.md)
- [New-session handoff design](../superpowers/specs/2026-07-19-new-session-handoff-design.md)
- [Live-contract stabilization design](../superpowers/specs/2026-07-19-live-contract-stabilization-design.md)
- [Live-contract stabilization implementation plan](../superpowers/plans/2026-07-19-live-contract-stabilization.md)
- [Compatibility runbook](../runbooks/vibe-compatibility.md)
- [Pinned compatibility baseline](../../compatibility/baseline.json)
- [Operator setup and development commands](../../README.md)

## Verification commands

```bash
uv sync --frozen --extra dev
uv lock --check
uv run ruff check src tests
uv run mypy src
uv run pytest -m "not contract" --cov=vibe_portfolio --cov-report=term-missing --cov-fail-under=85
```

The route-only, live runtime, and full MCP commands are intentionally documented in the [compatibility runbook](../runbooks/vibe-compatibility.md). Omitted opt-in flags mean skipped/not run, not passed. The runtime and MCP gates may contact a configured provider or consume model budget.

## Local integration evidence

- AInvest is running from the separate read-only `/Users/zhaibin/Dev/AInvest` checkout on `127.0.0.1:8899`; no AInvest source was changed.
- The isolated AInvest Python 3.11 environment is `/tmp/vibe-trading-portfolio-ainvest-venv311`. Its locked install required preinstalling `mini-racer==0.14.1` because the upstream requirements lock omitted the `mini-racer>=0.12.4` dependency required by pinned AkShare.
- The Sidecar API is listening on `127.0.0.1:8765`; it intentionally has no `/health` route. The Sidecar MCP server is listening on bearer-authenticated `127.0.0.1:8766/mcp`.
- The generated operator bundle remains under ignored `var/install`. The existing Vibe operator configuration backup is `/Users/zhaibin/.vibe-trading/agent.json.codex-backup-20260719-portfolio-mcp`. No token value is recorded here.
- A diagnostic test reproduced the runtime race: initial cancel returned `no_active_loop` at 0.007 seconds, while a retry for the same Session returned `cancelled` at 0.264 seconds. This evidence motivated the final same-Session retry fix.

## Current decisions and risks

- The sidecar preserves the former unrelated remote initial commit and `LICENSE` through merge commit `6b1ee52`. Future pushes can use normal fast-forward semantics; force-push remains unnecessary.
- Vibe compatibility is intentionally limited to `>=0.1.11,<0.2.0`; widening it requires updated fixtures and passing layered gates.
- Live runtime and MCP results are explicit dated local evidence, not a substitute for rerunning them after dependency, provider, operator configuration, or upstream changes.
- Experience Milestone 1A is an explicitly staged current-position snapshot experience, not the umbrella design's formal immutable-ledger MVP. It uses one fixed currency per account and never emits a cross-currency total.
- Market search and quotes will be implemented inside the Sidecar through isolated provider adapters; they will not depend on Vibe-Trading or import AInvest internals. Quotes refresh only on explicit user action, and failed refreshes preserve and visibly mark the last valid quote as stale.
- The first WebUI is a same-origin React/TypeScript SPA served by FastAPI on loopback. It has no login; Host/Origin/Fetch Metadata/CSP controls are mandatory compensations, and non-loopback deployment remains blocked.
- Public quote endpoints remain replaceable external dependencies with availability and usage-condition risk. Default tests must use fakes; real-provider smoke tests are opt-in and skipped means not run.
- Snapshot holdings cannot provide transaction-derived cost basis, realized returns, or reconciled performance. A future ledger migration must create explicit opening events with provenance and must not invent transaction history.

## Current blockers

Current code/test blockers: none known. The Experience Milestone 1A design audit resolved its architecture, data, security, failure, and test-boundary conflicts. The written specification still requires final user review before an implementation plan is produced.

## Recommended next step

Have the user review the written [Experience Milestone 1A design](../superpowers/specs/2026-07-19-portfolio-experience-webui-design.md). After explicit approval, use the planning workflow to produce a TDD implementation plan covering backend persistence/API, independent provider adapters, the React WebUI, security middleware, migrations, hermetic E2E, and retained compatibility gates.

## Latest design verification

- The new specification was checked with `git diff --cached --check`; no whitespace errors remained before commit.
- Its relative links to the umbrella design and current handoff resolve locally.
- A live CodeGraph audit confirmed that the current FastAPI app has three compatibility operations composed in `create_app`; the new design preserves their semantics while intentionally replacing the old exact-total-route test with a scoped compatibility-route invariant.
- The audit records 15 resolved findings and six accepted residual risks, including the snapshot-versus-ledger staging boundary, loopback no-auth limitation, quote-provider instability, and non-calendar-aware freshness rule.
- No Python, frontend, live-provider, Vibe runtime, or MCP test gate was rerun because this change is documentation-only. The previously recorded test results remain dated evidence, not fresh verification.

## End-of-session update checklist

- Reconcile this document with live branch, HEAD, recent commits, and remotes.
- Move work to completed only after recording fresh verification evidence.
- Update implemented and explicitly unimplemented scope.
- Record approved decisions, blockers, risks, and external dependencies without secrets.
- Replace the recommended next step with the next approval-gated action.
- Keep detailed setup and compatibility procedures in README/runbooks; link instead of duplicating them.
- Review the diff for personal holdings, account data, tokens, keys, and unsupported live-pass claims before committing.
