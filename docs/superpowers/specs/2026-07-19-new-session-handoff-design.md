# New-Session Handoff Documentation Design

**Date:** 2026-07-19  
**Status:** Approved

## Purpose

Create a small, durable handoff system that lets a new Codex session understand the Vibe-Trading Portfolio sidecar before changing it. The new session must report its understanding and proposed next step to the user, then wait for approval before beginning design or implementation work.

The handoff system must not duplicate the existing product design, implementation plan, README, or compatibility runbook. It should provide a stable entry point and link to those authoritative documents.

## Chosen structure

Use a two-layer structure:

1. Root `AGENTS.md` contains stable project governance and agent instructions.
2. `docs/handoff/CURRENT.md` contains the current, intentionally mutable handoff state.

Add a short handoff link to `README.md` so a human or a new session can find both documents immediately.

This structure is preferred over a single large `AGENTS.md` because stable rules should not churn whenever project status changes. A larger governance package with separate roadmap and decision-log files is deferred until the project needs that maintenance overhead.

## `AGENTS.md` responsibilities

`AGENTS.md` is the long-lived project charter. It will define:

- the repository's role as an independent, pluggable sidecar for Vibe-Trading;
- the boundary between this repository and `/Users/zhaibin/Dev/AInvest`;
- permitted integration through public REST, OpenAPI, SSE, and operator-installed MCP only;
- prohibited Vibe internal imports, shared storage, runtime patches, Session `mcpServers`, `ALLOW_SESSION_MCP_SERVERS=1`, broker writes, order placement, and trade execution;
- repository navigation and the authoritative design, plan, runbook, and handoff links;
- mandatory use of CodeGraph for structural questions when the repository has a healthy index, with native search reserved for literal text and known files;
- development, type-checking, linting, test, coverage, and compatibility commands;
- secret-handling and live-contract safety rules;
- the new-session startup protocol;
- the end-of-session handoff update protocol.

The startup protocol is:

1. Read `AGENTS.md` completely.
2. Read `docs/handoff/CURRENT.md` completely.
3. Inspect Git status, branch, HEAD, recent commits, and configured remotes.
4. Compare live repository state with the handoff document and call out drift.
5. Read only the directly relevant authoritative documents and source areas.
6. Report the understood state, constraints, risks, and recommended next step to the user.
7. Wait for explicit user approval before modifying code or starting the next milestone.

## `CURRENT.md` responsibilities

`docs/handoff/CURRENT.md` is the single dynamic handoff record. It will contain:

- project objective and current milestone;
- current verified capabilities;
- explicitly unimplemented scope;
- Vibe baseline SHA and supported version range;
- links to the approved design, Milestone 0 implementation plan, README, compatibility runbook, and baseline fixture;
- hard safety and isolation boundaries;
- last-known branch, commit, verification results, and remote state;
- setup and verification commands that a new session can run directly;
- current blockers, risks, external dependencies, and decisions that require the user;
- a recommended next step that is advisory and requires approval;
- an end-of-session checklist for updating the handoff.

The initial state will record that Milestone 0 is merged into sidecar `main`. It will identify `3b8e502` as the last-known HEAD while explicitly requiring every new session to verify the live HEAD rather than trusting the document. It will state that the repository currently has no Git remote.

The initial state will distinguish implemented compatibility infrastructure from the unimplemented portfolio product. Implemented scope includes the typed Vibe gateway, compatibility negotiation, bounded SSE recovery, authenticated read-only MCP, research-only Session coordination, diagnostic API and CLI, and layered release gates. Unimplemented scope includes the holdings ledger, imports, valuation, portfolio analytics, recommendation workflows, scheduling, and UI.

The recommended next milestone is to design the holdings domain model and local storage boundary. A new session must not start that work without user approval.

## Update rules

- Write an item under completed work only after the relevant verification has passed.
- Update routine status only in `CURRENT.md`.
- Modify `AGENTS.md` only when stable governance, safety boundaries, required workflow, or authoritative commands change.
- Never record API keys, bearer tokens, personal holdings, account identifiers, or other secrets in either document.
- Never describe an opt-in live gate as passed unless it actually ran against the configured external service.
- Treat commit IDs and test counts in `CURRENT.md` as last-known evidence, not current truth; verify them at session start.
- If the repository and `CURRENT.md` disagree, report the drift before editing and update the handoff as part of the approved work.
- At the end of a material session, update milestone status, verified results, decisions, blockers, next step, branch/HEAD, and remote state before handoff.

## README integration

Add a concise `New session handoff` section near the start of `README.md`. It will link to `AGENTS.md` and `docs/handoff/CURRENT.md` and state that agents must report their understanding and wait for approval before continuing milestone work.

The README remains the operator-facing setup entry point. It will not duplicate the complete handoff state or agent rules.

## Validation

The documentation change is accepted when:

- `AGENTS.md`, `docs/handoff/CURRENT.md`, and their linked authoritative files exist;
- all repository-relative Markdown links resolve;
- the actual commands and file paths named in the handoff match the repository;
- the documents contain no `TODO`, `TBD`, placeholders, sensitive values, contradictory milestone claims, or unsupported live-pass claims;
- the root `AGENTS.md` and dynamic handoff have non-overlapping responsibilities;
- a new session can read the two entry documents and accurately state the current scope, safety boundaries, verification commands, and approval-gated next step;
- Git diff review shows documentation-only changes with no application or upstream modifications.

## Out of scope

- Implementing the holdings domain, storage, imports, analytics, recommendations, scheduling, or UI.
- Creating or configuring a remote Git repository.
- Changing Vibe-Trading or its configuration.
- Adding an automated session-state database or generated handoff system.
- Replacing the approved product design, Milestone 0 plan, or compatibility runbook.
