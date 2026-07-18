# Live Contract Stabilization Design

**Date:** 2026-07-19
**Scope:** Vibe-Trading Portfolio sidecar only

## Objective

Make the opt-in live runtime and full MCP compatibility gates pass against the pinned Vibe-Trading `0.1.11` baseline without modifying AInvest, weakening fail-closed behavior, or allowing extra MCP tool calls.

## Observed failures

### Runtime cancellation race

Vibe emits `attempt.started` before its `SessionService` registers the active agent loop. A cancellation issued in that interval returns `no_active_loop`. The terminal assistant message for the same attempt can appear shortly afterward, but the verifier currently checks messages only once and fails before that proof is persisted.

### MCP probe goal-tool pollution

The probe creates an active finance research goal before sending its message. Vibe injects goal instructions that require `add_goal_evidence` and `update_research_goal_status`, so the attempt calls the intended Portfolio capability tool once and also calls internal goal-management tools. The strict verifier correctly rejects the extra calls.

## Design

### Bounded terminal-proof polling

When cancellation returns `no_active_loop`, the runtime verifier will poll the public messages route for a bounded period. It will pass only after finding an assistant message that:

- belongs to the original session;
- has `linked_attempt_id` equal to the original attempt;
- carries an explicit terminal status accepted by the existing verifier.

Polling uses the verifier's injected sleep function and a small explicit interval. Exhausting the existing event timeout without exact proof remains `cancel_not_proven_for_attempt`. Gateway errors and malformed responses continue to fail closed.

### Goal-free compatibility probe attempt

The MCP compatibility probe will create a normal public Session and send the existing bounded, research-only safety message directly. It will not create an active finance research goal for this protocol-only attempt.

This removes Vibe's mandatory goal-ledger instructions while retaining all probe safety properties:

- only public REST, SSE, and operator-installed MCP are used;
- the message still prohibits orders, broker writes, trade execution, and portfolio mutation;
- the watcher remains bound to the created session and accepted attempt;
- success still requires exactly one `mcp_portfolio_portfolio_get_capabilities` call and exactly one later successful result;
- any additional tool call or result still fails the gate.

Ordinary portfolio research continues to use `ResearchCoordinator` and a `research_general` goal. Only the narrow compatibility probe bypasses goal creation.

## Alternatives rejected

### Ignore goal-management tools

Allowlisting Vibe's goal tools inside the verifier would make the current run pass, but it would weaken the documented exact-call invariant and could hide unrelated tool use.

### Modify AInvest

Moving Vibe's active-loop registration or adding a probe-specific goal protocol would couple the sidecar milestone to upstream patches and violate the repository's read-only upstream boundary.

### Add fixed sleeps

A fixed delay before cancellation would hide rather than model the race, remain timing-sensitive, and slow every live gate. Condition-based polling provides exact evidence and a deterministic timeout.

## Tests

TDD coverage will add:

1. `no_active_loop` followed by a delayed exact terminal message passes.
2. Delayed unrelated or non-terminal messages never satisfy cancellation proof.
3. Exhausting the bound retains `cancel_not_proven_for_attempt`.
4. MCP probe creates a Session and sends the probe without creating a goal.
5. The existing exact tool-call/result checks continue to reject missing, duplicate, reordered, unsuccessful, or additional events.

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
