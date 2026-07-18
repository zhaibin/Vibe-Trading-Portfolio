# New-Session Handoff Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stable root agent charter and a dynamic current-state handoff so a new session can safely orient itself, report its understanding, and wait for user approval before continuing work.

**Architecture:** Keep stable governance in root `AGENTS.md` and mutable project state in `docs/handoff/CURRENT.md`. Add only a short discovery link to `README.md`; existing design, plan, baseline, and runbook remain authoritative and are linked rather than copied.

**Tech Stack:** Markdown, Git, Python 3.11+ one-shot validation commands, `rg`.

## Global Constraints

- The sidecar remains independent from `/Users/zhaibin/Dev/AInvest`; do not modify the upstream repository.
- Integration with Vibe-Trading remains limited to public REST, OpenAPI, SSE, and operator-installed MCP.
- Never use Vibe internal imports, shared Vibe storage, runtime patches, Session `mcpServers`, or `ALLOW_SESSION_MCP_SERVERS=1`.
- Never add broker writes, order placement, trade execution, or personal holdings data to the handoff documents.
- A new session must report its understanding and wait for explicit user approval before modifying code or starting a milestone.
- `AGENTS.md` holds stable rules; routine status changes belong only in `docs/handoff/CURRENT.md`.
- Do not claim an opt-in live gate passed unless it actually ran against the configured external service.
- Do not stage or commit unrelated `.codegraph/` or `.cursor/` content.

---

### Task 1: Add the complete new-session handoff system

**Files:**
- Create: `AGENTS.md`
- Create: `docs/handoff/CURRENT.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: approved design `docs/superpowers/specs/2026-07-19-new-session-handoff-design.md`, existing product design, Milestone 0 plan, compatibility baseline, and runbook.
- Produces: root agent entry point `AGENTS.md`, dynamic handoff entry point `docs/handoff/CURRENT.md`, and README discovery links.

- [ ] **Step 1: Run the pre-implementation documentation contract and verify RED**

Run:

```bash
uv run python -c 'from pathlib import Path; required=[Path("AGENTS.md"),Path("docs/handoff/CURRENT.md")]; missing=[str(p) for p in required if not p.is_file()]; assert not missing, f"missing handoff files: {missing}"'
```

Expected: non-zero exit with `missing handoff files: ['AGENTS.md', 'docs/handoff/CURRENT.md']`.

- [ ] **Step 2: Create the stable root `AGENTS.md` charter**

Create `AGENTS.md` with this content:

```markdown
# Repository Agent Instructions

## Project role

This repository is the independent personal-portfolio sidecar for Vibe-Trading. Keep it pluggable: Vibe-Trading must remain upgradeable without carrying sidecar patches.

Before doing any work, read this file completely and then read [`docs/handoff/CURRENT.md`](docs/handoff/CURRENT.md).

## New-session startup protocol

1. Read `AGENTS.md` and `docs/handoff/CURRENT.md` completely.
2. Run `git status --short --branch`, `git rev-parse HEAD`, `git log --oneline -10`, and `git remote -v`.
3. Compare live Git state with the last-known evidence in `CURRENT.md`; report any drift.
4. Read only the authoritative documents and source areas relevant to the requested next step.
5. Report the understood state, constraints, risks, and recommended next step to the user.
6. Wait for explicit user approval before modifying code or starting the next milestone.

Do not infer approval from a previous session's plans or from `CURRENT.md`.

## Repository boundaries

- Treat `/Users/zhaibin/Dev/AInvest` as upstream and read-only unless the user explicitly starts a separate upstream task.
- Integrate only through Vibe's public REST API, OpenAPI document, SSE stream, and operator-installed MCP configuration.
- Do not import Vibe internals, share its database or private files, monkey-patch its runtime, or copy sidecar code into Vibe.
- Never set `ALLOW_SESSION_MCP_SERVERS=1` or send Session `mcpServers` overrides.
- MCP must remain loopback-only, bearer-authenticated, read-only, and explicitly allowlisted without wildcards.
- Never add broker writes, order placement, trade execution, or instructions that imply those capabilities.
- Keep generated tokens, MCP snippets, API keys, account identifiers, and personal holdings out of Git and handoff documents.

## Authoritative documents

- Current handoff: [`docs/handoff/CURRENT.md`](docs/handoff/CURRENT.md)
- Product design: [`docs/superpowers/specs/2026-07-18-personal-portfolio-sidecar-design.md`](docs/superpowers/specs/2026-07-18-personal-portfolio-sidecar-design.md)
- Milestone 0 plan: [`docs/superpowers/plans/2026-07-18-vibe-compatibility-spike.md`](docs/superpowers/plans/2026-07-18-vibe-compatibility-spike.md)
- Compatibility runbook: [`docs/runbooks/vibe-compatibility.md`](docs/runbooks/vibe-compatibility.md)
- Pinned upstream contract: [`compatibility/baseline.json`](compatibility/baseline.json)

Link to these sources instead of duplicating their detailed content.

## Code navigation

When a healthy CodeGraph index exists, prefer CodeGraph for structural questions such as symbol definitions, callers, callees, traces, and impact. Use `rg` or direct reads for literal text, comments, log messages, filenames, and files already identified. If CodeGraph reports pending files, read those specific files directly. If the repository is not initialized, ask before running `codegraph init -i` because it writes repository metadata.

## Required development workflow

- Preserve unrelated user changes and untracked files.
- Use TDD for behavior changes: demonstrate RED, implement the smallest fix, then demonstrate GREEN.
- Keep the sidecar fail-closed when Vibe is offline, not ready, unsupported, malformed, or missing verifiable MCP evidence.
- Do not describe opt-in live runtime or MCP gates as passed when they were skipped or not configured.
- Run review and verification proportional to risk before completion.

Baseline hermetic gate:

```bash
uv sync --frozen --extra dev
uv lock --check
uv run ruff check src tests
uv run mypy src
uv run pytest -m "not contract" --cov=vibe_portfolio --cov-report=term-missing --cov-fail-under=85
```

Follow [`docs/runbooks/vibe-compatibility.md`](docs/runbooks/vibe-compatibility.md) for opt-in route, runtime, and MCP contract gates.

## End-of-session handoff

After material approved work, update `docs/handoff/CURRENT.md` with verified capabilities, remaining scope, decisions, blockers, next recommended step, branch, relevant commits, remote state, and actual verification results. Update this file only when stable governance or required workflow changes. Never record secrets or personal portfolio data.
```

- [ ] **Step 3: Create the dynamic `CURRENT.md` handoff**

Create `docs/handoff/CURRENT.md` with this content:

```markdown
# Current Project Handoff

**Last updated:** 2026-07-19  
**Project:** Vibe-Trading Portfolio sidecar  
**Current milestone:** Milestone 0 compatibility foundation completed; portfolio product implementation not started

## Start here

This file is last-known evidence, not a substitute for inspecting the repository. A new session must first follow the startup protocol in [`AGENTS.md`](../../AGENTS.md), compare this handoff with live Git state, report its understanding and proposed next step, and wait for user approval before modifying code.

## Last-known repository state

- Branch: `main`
- Milestone 0 implementation head: `3b8e502` (`fix: reject pre-open token path replacement`)
- Handoff design commit: `9d4dfd5` (`docs: design new-session handoff`)
- Remote: none configured at the time of this update
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

The last hermetic verification associated with Milestone 0 reported 123 passed, 3 deselected, 90.24% coverage, clean Ruff, strict mypy, and lock checks. Treat these counts as historical evidence and rerun the gate before relying on them.

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

## Recommended next step

Propose and obtain user approval for a focused design of the holdings domain model and local storage boundary. The design should cover accounts, instruments, positions, transactions or snapshots, currency and precision, import idempotency, privacy, migrations, and the bounded portfolio context supplied to Vibe. Do not begin implementation from this recommendation alone.

## End-of-session update checklist

- Reconcile this document with live branch, HEAD, recent commits, and remotes.
- Move work to completed only after recording fresh verification evidence.
- Update implemented and explicitly unimplemented scope.
- Record approved decisions, blockers, risks, and external dependencies without secrets.
- Replace the recommended next step with the next approval-gated action.
- Keep detailed setup and compatibility procedures in README/runbooks; link instead of duplicating them.
- Review the diff for personal holdings, account data, tokens, keys, and unsupported live-pass claims before committing.
```

- [ ] **Step 4: Add the README discovery entry**

Insert this section after the opening two paragraphs and before `## Local setup` in `README.md`:

```markdown
## New session handoff

Agents and maintainers should start with [`AGENTS.md`](AGENTS.md) and the current [`docs/handoff/CURRENT.md`](docs/handoff/CURRENT.md). A new session must verify the live Git state, report its understanding and recommended next step, and wait for user approval before continuing milestone work.
```

- [ ] **Step 5: Run the documentation contract and verify GREEN**

Run:

```bash
uv run python -c 'from pathlib import Path; required=[Path("AGENTS.md"),Path("docs/handoff/CURRENT.md"),Path("README.md"),Path("docs/superpowers/specs/2026-07-18-personal-portfolio-sidecar-design.md"),Path("docs/superpowers/plans/2026-07-18-vibe-compatibility-spike.md"),Path("docs/runbooks/vibe-compatibility.md"),Path("compatibility/baseline.json")]; missing=[str(p) for p in required if not p.is_file()]; assert not missing, f"missing files: {missing}"; agents=Path("AGENTS.md").read_text(); current=Path("docs/handoff/CURRENT.md").read_text(); readme=Path("README.md").read_text(); assert "wait for explicit user approval" in agents; assert "Do not begin implementation" in current; assert "docs/handoff/CURRENT.md" in readme; print("handoff contract: pass")'
```

Expected: `handoff contract: pass` and exit 0.

- [ ] **Step 6: Validate all relative Markdown links**

Run:

```bash
uv run python -c 'import re; from pathlib import Path; files=[Path("AGENTS.md"),Path("docs/handoff/CURRENT.md"),Path("README.md")]; broken=[]; pattern=re.compile(r"\[[^]]+\]\(([^)]+)\)"); [(broken.append(f"{p}:{target}") if not (p.parent/target.split("#",1)[0]).resolve().exists() else None) for p in files for target in pattern.findall(p.read_text()) if not target.startswith(("http://","https://","#"))]; assert not broken, "broken links: " + ", ".join(broken); print("markdown links: pass")'
```

Expected: `markdown links: pass` and exit 0.

- [ ] **Step 7: Scan for unsafe or unfinished handoff content**

Run:

```bash
rg -n "FIXME|PLACEHOLDER|api[_-]?key\s*[:=]\s*[^$]|bearer\s+[A-Za-z0-9._-]{12,}|account[_-]?id\s*[:=]" AGENTS.md docs/handoff/CURRENT.md
```

Expected: no matches and exit 1 from `rg` because no unsafe text is present.

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; status lists only the three intended documentation paths plus unrelated pre-existing untracked files.

- [ ] **Step 8: Commit the handoff documentation**

```bash
git add AGENTS.md docs/handoff/CURRENT.md README.md
git commit -m "docs: add new-session handoff"
```

- [ ] **Step 9: Verify the committed result**

Run the GREEN documentation contract and Markdown-link validation commands again, followed by:

```bash
git show --stat --oneline HEAD
git status --short --branch
```

Expected: the commit contains only `AGENTS.md`, `docs/handoff/CURRENT.md`, and `README.md`; the tracked working tree is clean. Unrelated `.codegraph/` or `.cursor/` files may remain untracked and must not be staged.
