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
6. wait for explicit user approval before modifying code or starting the next milestone.

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
