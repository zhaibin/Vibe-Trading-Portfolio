# Current Project Handoff

**Last updated:** 2026-07-19
**Project:** Vibe-Trading Portfolio sidecar
**Current milestone:** Milestone 0 compatibility foundation completed; portfolio product implementation not started

## Start here

This file is last-known evidence, not a substitute for inspecting the repository. A new session must first follow the startup protocol in [`AGENTS.md`](../../AGENTS.md), compare this handoff with live Git state, and report its understanding and proposed next step. Before modifying any repository file or starting milestone work, wait for explicit user approval.

## Last-known repository state

The following state was verified after local integration on 2026-07-19. It remains last-known evidence rather than a substitute for inspecting the live checkout:

- Target/integration branch: `main`
- Last verified active branch: `main`
- Integrated handoff branch: `docs/new-session-handoff`
- Handoff branch base: `fb1e81e` (`docs: plan new-session handoff`)
- Handoff implementation commits: `0bbe561` (`docs: add new-session handoff`) and `16446c3` (`docs: clarify handoff approval gate`)
- Milestone 0 implementation head: `3b8e502` (`fix: reject pre-open token path replacement`)
- Handoff design commit: `9d4dfd5` (`docs: design new-session handoff`)
- Plan commit: `fb1e81e` (`docs: plan new-session handoff`)
- Remote: none configured at the time this handoff was written
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
- diagnostic system API and machine-readable compatibility CLI;
- pinned compatibility baseline, minimum/stable/latest CI matrix, layered route/runtime/MCP gates, and an 85% coverage threshold.

The latest hermetic verification after local integration reported 123 passed, 3 deselected, 90.24% coverage, clean Ruff, strict mypy, and lock checks. Treat these counts as historical evidence and rerun the gate before relying on them.

## Explicitly not implemented

- holdings ledger and transaction model;
- CSV, broker, or manual holdings import;
- valuation, cost basis, performance, exposure, concentration, or risk analytics;
- portfolio-aware Vibe context assembly beyond the compatibility probe;
- recommendation workflows, scheduling, alerts, or UI;
- broker connectivity, order placement, trade execution, or any write action.

Do not describe the project as having a usable personal portfolio module yet.

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
- [Milestone 0 implementation plan](../superpowers/plans/2026-07-18-vibe-compatibility-spike.md)
- [New-session handoff design](../superpowers/specs/2026-07-19-new-session-handoff-design.md)
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

## Current decisions and risks

- The sidecar has no configured Git remote, so work is local until the user approves remote creation and push.
- Vibe compatibility is intentionally limited to `>=0.1.11,<0.2.0`; widening it requires updated fixtures and passing layered gates.
- No live Vibe runtime or operator MCP result should be inferred from the hermetic suite.
- The next milestone must define privacy, retention, precision, currency, and migration rules before storing personal holdings.

## Current blockers

Current blockers: none known. External dependencies and decisions remain user approval to create and push a remote, if desired, and user approval for the future holdings-domain design before that milestone begins.

## Recommended next step

Propose and obtain user approval for a focused design of the holdings domain model and local storage boundary. The design should cover accounts, instruments, positions, transactions or snapshots, currency and precision, import idempotency, privacy, migrations, and the bounded portfolio context supplied to Vibe. Before modifying any repository file or starting milestone work, wait for explicit user approval.

## End-of-session update checklist

- Reconcile this document with live branch, HEAD, recent commits, and remotes.
- Move work to completed only after recording fresh verification evidence.
- Update implemented and explicitly unimplemented scope.
- Record approved decisions, blockers, risks, and external dependencies without secrets.
- Replace the recommended next step with the next approval-gated action.
- Keep detailed setup and compatibility procedures in README/runbooks; link instead of duplicating them.
- Review the diff for personal holdings, account data, tokens, keys, and unsupported live-pass claims before committing.
