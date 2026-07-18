# Live Contract Stabilization Design

**Date:** 2026-07-19
**Scope:** Vibe-Trading Portfolio sidecar only

## Objective

Make the opt-in live runtime and full MCP compatibility gates pass against the pinned Vibe-Trading `0.1.11` baseline without modifying AInvest, weakening fail-closed behavior, or permitting unrelated, broker, execution, or unknown tool calls.

## Observed failures

### Runtime cancellation race

Vibe emits `attempt.started` before its `SessionService` registers the active agent loop. A cancellation issued in that interval returns `no_active_loop`, while the attempt continues running. A live diagnostic reproduced the race: the initial cancel returned `no_active_loop` at 0.007 seconds and a retry against the same Session returned `cancelled` at 0.264 seconds. Without a retry, a terminal assistant message may take longer than the 30-second live gate and the probe does not actually prove cancellation.

### MCP probe goal-tool pollution

The probe creates an active finance research goal before sending its message. Vibe injects goal instructions that require `add_goal_evidence` and `update_research_goal_status`, so the attempt calls the intended Portfolio capability tool once and also calls internal goal-management tools. The strict verifier correctly rejects the extra calls.

## Design

### Bounded cancellation and terminal-proof polling

When cancellation returns `no_active_loop`, the runtime verifier will first check the public messages route for exact terminal proof. While no terminal proof exists, each additional bounded attempt will sleep for the configured interval, retry cancellation against the same original Session, and:

- pass immediately if cancellation returns `cancelled`;
- check messages again if cancellation still returns `no_active_loop`;
- fail closed if cancellation returns any other status.

Terminal-message proof passes only after finding an assistant message that:

- belongs to the original session;
- has `linked_attempt_id` equal to the original attempt;
- carries an explicit terminal status accepted by the existing verifier.

The verifier will expose a positive `terminal_poll_interval_seconds` constructor parameter with a default of `0.25`. After an immediate message check, it may perform at most
`ceil(event_timeout_seconds / terminal_poll_interval_seconds)` additional sleep, cancel, and conditional message-check attempts. A zero event timeout therefore performs only the immediate terminal-message check and no cancel retry. The accepted terminal metadata statuses remain the existing explicit set: `completed`, `failed`, and `cancelled`.

Exhausting that deterministic attempt bound without exact proof remains `cancel_not_proven_for_attempt`. Gateway errors, malformed responses, invalid polling intervals, and unrelated or non-terminal messages continue to fail closed.

### Research-goal-compatible MCP event validation

The MCP compatibility probe will continue to use `ResearchCoordinator`, create a `research_general` goal, register cleanup responsibility immediately after Session creation, and send the existing bounded safety message. Existing best-effort cancellation remains responsible for the original Session until an exact terminal outcome is proven.

Vibe `0.1.11` requires an active research goal to record tool-backed evidence and close its audit ledger. The event validator will therefore distinguish the Portfolio MCP boundary under test from Vibe's explicitly allowed research-goal control plane:

- only public REST, SSE, and operator-installed MCP are used;
- Session payloads remain free of `mcpServers` and Session MCP overrides;
- every Agent message remains bounded to 4,000 characters, classified by a `research_general` goal, and suffixed with the no-trading safety instructions;
- the message still prohibits orders, broker writes, trade execution, and portfolio mutation;
- the watcher remains bound to the created session and accepted attempt;
- success still requires exactly one `mcp_portfolio_portfolio_get_capabilities` call and exactly one later successful result;
- the only additional tool calls/results permitted are the Vibe research-goal control tools `get_research_goal`, `add_goal_evidence`, and `update_research_goal_status`;
- every permitted goal-control result must name a previously observed permitted goal-control call and report `status="ok"`;
- any other Portfolio MCP tool, non-allowlisted Vibe tool, broker tool, execution tool, write tool, unknown tool, duplicate target call/result, reordered target result, or unsuccessful target result fails the gate.

The probe result will continue to report all observed tool names for diagnosis. Allowing the three goal-control tools does not count them as evidence that the Portfolio MCP boundary is available; only the exact target call/result pair can produce `available`.

## Alternatives rejected

### Ignore all non-Portfolio tools

Ignoring every non-Portfolio tool would make the current run pass, but it could hide unrelated or unsafe tool use. The design instead permits only three named Vibe research-goal control tools and rejects every other non-target tool.

### Bypass research goal creation

Sending the probe without a goal would avoid Vibe's goal-control calls, but it would violate the Milestone 0 requirement that Agent messages use `risk_tier="research_general"` and would bypass the established research-only coordination boundary.

### Modify AInvest

Moving Vibe's active-loop registration or adding a probe-specific goal protocol would couple the sidecar milestone to upstream patches and violate the repository's read-only upstream boundary.

### Add one fixed sleep

A single fixed delay before cancellation would hide rather than model the race, remain timing-sensitive, and slow every live gate. Bounded retries against the same Session provide exact cancellation or terminal evidence and a deterministic timeout.

## Tests

TDD coverage will add:

1. `no_active_loop` followed by a successful cancel retry against the same Session passes; delayed exact terminal proof remains an accepted fallback.
2. Delayed unrelated or non-terminal messages never satisfy cancellation proof.
3. A zero timeout performs one immediate terminal check; exhausting the deterministic bound retains `cancel_not_proven_for_attempt`.
4. Invalid terminal polling intervals fail closed during verifier construction.
5. MCP probe continues to create a `research_general` goal, keeps Session payloads free of `mcpServers`, and preserves bounded no-trading messages and failure cleanup.
6. The exact target call/result passes when interleaved only with successful, ordered results for the three allowlisted goal-control tools.
7. Orphaned or unsuccessful goal-control results and missing, duplicate, reordered, unsuccessful, additional Portfolio MCP, broker, execution, write, unknown, or other non-allowlisted tool events still fail the gate.

Verification after implementation:

- focused RED/GREEN unit tests;
- the full hermetic Ruff, mypy, pytest, and coverage gate;
- route-only compatibility gate;
- opt-in live runtime contract against `127.0.0.1:8899`;
- opt-in full MCP probe against the operator-installed loopback MCP server.

## Non-goals

- No AInvest source changes.
- No relaxation of MCP authentication, loopback binding, or tool allowlisting.
- No broker, order, execution, or other write capability.
- No changes to holdings-domain scope or future portfolio product milestones.
