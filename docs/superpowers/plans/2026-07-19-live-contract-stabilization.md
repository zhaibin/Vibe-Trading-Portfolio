# Live Contract Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the opt-in live runtime and full MCP gates pass against Vibe-Trading `0.1.11` while preserving exact attempt correlation and fail-closed tool validation.

**Architecture:** Extend `RuntimeContractVerifier` with a deterministic same-Session cancel-retry and terminal-message polling helper used only after `cancel` returns `no_active_loop`. Refactor the MCP outcome validator into an ordered event scan that accepts the one required Portfolio capability call/result plus only three named, correlated Vibe research-goal control call/result pairs.

**Tech Stack:** Python 3.11, asyncio, Pydantic DTOs, pytest/pytest-asyncio, Ruff, mypy, uv.

## Global Constraints

- `/Users/zhaibin/Dev/AInvest` remains read-only; make every source and test change in this repository.
- Use only Vibe public REST, OpenAPI, SSE, and operator-installed MCP configuration.
- Never add Session `mcpServers` overrides or enable `ALLOW_SESSION_MCP_SERVERS`.
- Keep MCP loopback-only, bearer-authenticated, read-only, and explicitly allowlisted without wildcards.
- Every Agent message remains at most 4,000 characters, uses a `research_general` goal, and includes the existing no-trading safety suffix.
- Do not add broker writes, order placement, trade execution, portfolio mutation, or wording that implies those capabilities.
- Fail closed on offline, unready, unsupported, malformed, uncorrelated, reordered, duplicated, unsuccessful, or unknown evidence.
- Preserve unrelated changes and the untracked `.codegraph/` and `.cursor/` directories.

---

## File Structure

- Modify `src/vibe_portfolio/vibe/contract.py`: own bounded same-Session cancellation retry, terminal-proof polling, and polling interval validation.
- Modify `tests/vibe/test_runtime_contract.py`: prove delayed exact terminal evidence succeeds and all polling bounds/identity failures remain closed.
- Modify `src/vibe_portfolio/vibe/mcp_probe.py`: own the target/goal-control tool allowlists and ordered call/result validation.
- Modify `tests/vibe/test_mcp_probe.py`: prove the allowlisted Vibe control-plane sequence passes and unsafe, orphaned, duplicated, reordered, or unsuccessful evidence fails.
- Modify `docs/handoff/CURRENT.md`: record actual commits, remote state, live process configuration, verification results, and remaining limitations without secrets.

### Task 1: Deterministic cancellation retry and terminal-proof polling

**Files:**
- Modify: `src/vibe_portfolio/vibe/contract.py:76-89,192-208,320-333`
- Test: `tests/vibe/test_runtime_contract.py:130-204`

**Interfaces:**
- Consumes: `RuntimeGateway.list_messages(session_id: str, limit: int = 100) -> list[MessageRecord]` and the existing `_has_terminal_attempt_message(...) -> bool` identity check.
- Produces: `RuntimeContractVerifier(..., terminal_poll_interval_seconds: float = 0.25, ...)` and `_prove_cancelled_or_terminal(session_id: str, attempt_id: str) -> bool`.

- [ ] **Step 1: Write failing polling-bound and delayed-proof tests**

Add a scripted gateway and tests to `tests/vibe/test_runtime_contract.py`:

```python
class ScriptedNoActiveLoopGateway(UnprovenNoActiveLoopGateway):
    def __init__(
        self,
        terminal_checks: list[list[MessageRecord]],
        *,
        cancel_statuses: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.terminal_checks = terminal_checks
        self.cancel_statuses = cancel_statuses or ["no_active_loop"]
        self.poll_calls = 0

    async def cancel(self, session_id: str) -> CancelResult:
        self.cancel_calls.append(session_id)
        status_index = min(len(self.cancel_calls) - 1, len(self.cancel_statuses) - 1)
        return CancelResult(status=self.cancel_statuses[status_index])

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        self.poll_calls += 1
        messages = await super().list_messages(session_id, limit)
        terminal_check_index = self.poll_calls - 2
        if 0 <= terminal_check_index < len(self.terminal_checks):
            messages.extend(self.terminal_checks[terminal_check_index])
        return messages


def terminal_message(
    *, session_id: str = "session-1", attempt_id: str = "attempt-1", status: str = "completed"
) -> MessageRecord:
    return MessageRecord(
        message_id="assistant-1",
        session_id=session_id,
        role="assistant",
        content="done",
        created_at="2026-07-18T00:00:02Z",
        linked_attempt_id=attempt_id,
        metadata={"status": status},
    )


async def test_no_active_loop_polls_until_delayed_exact_terminal_proof() -> None:
    gateway = ScriptedNoActiveLoopGateway([[], [terminal_message()]])
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = await RuntimeContractVerifier(
        gateway,
        FakeDiscovery(),
        stream_warmup_seconds=0,
        event_timeout_seconds=0.5,
        terminal_poll_interval_seconds=0.25,
        sleep=record_sleep,
    ).verify()

    assert result.passed is True
    assert gateway.poll_calls == 3
    assert sleeps == [0, 0.25]


async def test_no_active_loop_retries_cancel_after_registration_race() -> None:
    gateway = ScriptedNoActiveLoopGateway(
        [[]], cancel_statuses=["no_active_loop", "cancelled"]
    )
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = await RuntimeContractVerifier(
        gateway,
        FakeDiscovery(),
        stream_warmup_seconds=0,
        event_timeout_seconds=0.5,
        terminal_poll_interval_seconds=0.25,
        sleep=record_sleep,
    ).verify()

    assert result.passed is True
    assert gateway.cancel_calls == ["session-1", "session-1"]
    assert gateway.poll_calls == 2
    assert sleeps == [0, 0.25]


async def test_zero_timeout_checks_terminal_messages_once_without_sleeping() -> None:
    gateway = ScriptedNoActiveLoopGateway([[]])
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    verifier = RuntimeContractVerifier(
        gateway,
        FakeDiscovery(),
        event_timeout_seconds=0,
        terminal_poll_interval_seconds=0.25,
        sleep=record_sleep,
    )

    proven = await verifier._prove_cancelled_or_terminal("session-1", "attempt-1")

    assert proven is False
    assert gateway.poll_calls == 1
    assert sleeps == []


@pytest.mark.parametrize("interval", [0, -0.25])
def test_terminal_poll_interval_must_be_positive(interval: float) -> None:
    with pytest.raises(ValueError, match="terminal_poll_interval_seconds must be positive"):
        RuntimeContractVerifier(
            FakeRuntimeGateway(),
            FakeDiscovery(),
            terminal_poll_interval_seconds=interval,
        )
```

Extend the scripted test matrix so messages with the wrong session, wrong attempt, non-assistant role, or status outside `completed`, `failed`, and `cancelled` exhaust the bound and return `cancel_not_proven_for_attempt`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run pytest tests/vibe/test_runtime_contract.py -q
```

Expected: the new constructor argument and `_prove_cancelled_or_terminal` helper are absent, and the registration-race test returns `cancel_not_proven_for_attempt` before implementation.

- [ ] **Step 3: Implement the minimal bounded cancellation-proof helper**

In `src/vibe_portfolio/vibe/contract.py`, import `math`, validate the new constructor input, and store it:

```python
import math

# in RuntimeContractVerifier.__init__ keyword-only arguments
terminal_poll_interval_seconds: float = 0.25,

if terminal_poll_interval_seconds <= 0:
    raise ValueError("terminal_poll_interval_seconds must be positive")
self.terminal_poll_interval_seconds = terminal_poll_interval_seconds
```

Replace the single `no_active_loop` message fetch with:

```python
if cancel_result.status == "no_active_loop":
    if not await self._prove_cancelled_or_terminal(session_id, attempt_id):
        return self._failed(
            stage,
            "cancel_not_proven_for_attempt",
            version=version,
            session_id=session_id,
            attempt_id=attempt_id,
        )
```

Add the helper next to `_has_terminal_attempt_message`; it checks exact terminal proof immediately, then retries cancel against the same Session after each bounded interval and polls messages again only while Vibe continues to report `no_active_loop`:

```python
async def _prove_cancelled_or_terminal(self, session_id: str, attempt_id: str) -> bool:
    additional_checks = math.ceil(
        max(self.event_timeout_seconds, 0) / self.terminal_poll_interval_seconds
    )
    messages = await self._bounded(self.gateway.list_messages(session_id, limit=100))
    if self._has_terminal_attempt_message(messages, session_id, attempt_id):
        return True
    for _ in range(additional_checks):
        await self.sleep(self.terminal_poll_interval_seconds)
        cancel_result = await self._bounded(self.gateway.cancel(session_id))
        if cancel_result.status == "cancelled":
            return True
        if cancel_result.status != "no_active_loop":
            return False
        messages = await self._bounded(self.gateway.list_messages(session_id, limit=100))
        if self._has_terminal_attempt_message(messages, session_id, attempt_id):
            return True
    return False
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/vibe/test_runtime_contract.py -q
```

Expected: all runtime contract unit tests pass, including same-Session cancel retry, exact terminal identity, zero-timeout, deterministic exhaustion, and interval validation cases.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/vibe_portfolio/vibe/contract.py tests/vibe/test_runtime_contract.py
git commit -m "fix: retry runtime cancel registration race"
```

### Task 2: Strict research-goal-compatible MCP event validation

**Files:**
- Modify: `src/vibe_portfolio/vibe/mcp_probe.py:8-13,106-190`
- Test: `tests/vibe/test_mcp_probe.py:151-390`

**Interfaces:**
- Consumes: ordered `AttemptOutcome.events: tuple[SseEvent, ...]` for the exact accepted attempt.
- Produces: `ALLOWED_GOAL_CONTROL_TOOLS: frozenset[str]` and `_result_for_outcome(...) -> McpProbeResult` that accepts only a single successful target pair plus correlated successful goal-control pairs.

- [ ] **Step 1: Write failing allowlisted-control-plane tests**

Add this constant import and helpers to `tests/vibe/test_mcp_probe.py`:

```python
from vibe_portfolio.vibe.mcp_probe import (
    ALLOWED_GOAL_CONTROL_TOOLS,
    EXPECTED_VIBE_TOOL_NAME,
    McpProbeResult,
    PortfolioMcpProbe,
)


def tool_event(event_id: str, event_type: str, tool: str, *, status: str | None = None) -> SseEvent:
    data: dict[str, object] = {"attempt_id": "attempt-1", "tool": tool}
    if status is not None:
        data["status"] = status
    return SseEvent(event_id, event_type, data)
```

Add the success case:

```python
async def test_probe_accepts_correlated_successful_goal_control_pairs() -> None:
    get_goal, add_evidence, update_status = sorted(ALLOWED_GOAL_CONTROL_TOOLS)
    events = (
        tool_event("e1", "tool_call", get_goal),
        tool_event("e2", "tool_result", get_goal, status="ok"),
        tool_event("e3", "tool_call", EXPECTED_VIBE_TOOL_NAME),
        tool_event("e4", "tool_result", EXPECTED_VIBE_TOOL_NAME, status="ok"),
        tool_event("e5", "tool_call", add_evidence),
        tool_event("e6", "tool_result", add_evidence, status="ok"),
        tool_event("e7", "tool_call", update_status),
        tool_event("e8", "tool_result", update_status, status="ok"),
        SseEvent("e9", "attempt.completed", {"attempt_id": "attempt-1"}),
    )

    result = await PortfolioMcpProbe(FakeGateway(), FakeWatcher(outcome_with(*events))).run()

    assert result.status is McpStatus.AVAILABLE
    assert result.observed_tools == [
        get_goal,
        EXPECTED_VIBE_TOOL_NAME,
        add_evidence,
        update_status,
    ]
```

Add parameterized failures for:

```python
@pytest.mark.parametrize(
    ("events", "reason"),
    [
        ((tool_event("e1", "tool_result", "add_goal_evidence", status="ok"),), "orphaned_tool_result"),
        (
            (
                tool_event("e1", "tool_call", "add_goal_evidence"),
                tool_event("e2", "tool_result", "add_goal_evidence", status="error"),
            ),
            "goal_control_result_not_successful",
        ),
        ((tool_event("e1", "tool_call", "place_order"),), "unexpected_tool_calls_observed"),
        ((tool_event("e1", "tool_result", "unknown_tool", status="ok"),), "unexpected_tool_results_observed"),
    ],
)
async def test_probe_rejects_unsafe_or_uncorrelated_control_events(
    events: tuple[SseEvent, ...], reason: str
) -> None:
    complete_events = (
        *events,
        tool_event("target-call", "tool_call", EXPECTED_VIBE_TOOL_NAME),
        tool_event("target-result", "tool_result", EXPECTED_VIBE_TOOL_NAME, status="ok"),
        SseEvent("terminal", "attempt.completed", {"attempt_id": "attempt-1"}),
    )

    result = await PortfolioMcpProbe(
        FakeGateway(), FakeWatcher(outcome_with(*complete_events))
    ).run()

    assert result.status is McpStatus.FAILED
    assert result.reason == reason
```

Retain and extend the existing target tests to cover a missing call/result, duplicate target call/result, target result before call, target `status="error"`, another Portfolio MCP tool, and same-named events from another attempt. Keep the existing assertions that `ResearchCoordinator` creates a goal, the probe message contains the no-order/broker-write/trade-execution clauses, is at most 4,000 characters, and contains no `mcpServers`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run pytest tests/vibe/test_mcp_probe.py -q
```

Expected: collection fails because `ALLOWED_GOAL_CONTROL_TOOLS` is not defined, or the interleaved allowlisted sequence fails with `unexpected_tool_calls_observed` before implementation.

- [ ] **Step 3: Implement ordered fail-closed validation**

Define the exact control-plane allowlist in `src/vibe_portfolio/vibe/mcp_probe.py`:

```python
ALLOWED_GOAL_CONTROL_TOOLS = frozenset(
    {"get_research_goal", "add_goal_evidence", "update_research_goal_status"}
)
ALLOWED_PROBE_TOOLS = ALLOWED_GOAL_CONTROL_TOOLS | {EXPECTED_VIBE_TOOL_NAME}
```

In `_result_for_outcome`, retain the current outcome/session/attempt status checks, then scan same-attempt events in order. Track `pending_calls: dict[str, int]`, `target_call_index`, and `target_result_index`. For each `tool_call`, reject names outside `ALLOWED_PROBE_TOOLS`, reject a second target call, record the observed name, and increment its pending count. For each `tool_result`, reject names outside the allowlist, reject a result with no pending earlier same-name call, decrement the pending count, require `status == "ok"`, and reject a second target result. After the scan, return:

```python
if target_call_index is None:
    return McpProbeResult(
        McpStatus.MISSING,
        session_id,
        attempt_id,
        observed_tools,
        "expected_tool_call_not_observed",
    )
if target_result_index is None:
    return McpProbeResult(
        McpStatus.FAILED,
        session_id,
        attempt_id,
        observed_tools,
        "tool_result_not_successful",
    )
if target_result_index <= target_call_index:
    return McpProbeResult(
        McpStatus.FAILED,
        session_id,
        attempt_id,
        observed_tools,
        "tool_result_not_after_call",
    )
return McpProbeResult(McpStatus.AVAILABLE, session_id, attempt_id, observed_tools)
```

Use stable failure reasons asserted by the tests: `unexpected_tool_calls_observed`, `unexpected_tool_results_observed`, `orphaned_tool_result`, `goal_control_result_not_successful`, `tool_result_not_after_call`, and `tool_result_not_successful`. Do not treat pending goal-control calls without results as success; return `goal_control_result_not_successful` after the scan when any goal-control pending count remains positive.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/vibe/test_mcp_probe.py -q
```

Expected: every MCP unit test passes; observed tool names include the allowlisted control-plane calls, but only the exact Portfolio target pair produces `McpStatus.AVAILABLE`.

- [ ] **Step 5: Run static checks for both implementation tasks**

Run:

```bash
uv run ruff check src/vibe_portfolio/vibe/contract.py src/vibe_portfolio/vibe/mcp_probe.py tests/vibe/test_runtime_contract.py tests/vibe/test_mcp_probe.py
uv run mypy src
```

Expected: both commands exit `0` with no diagnostics.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/vibe_portfolio/vibe/mcp_probe.py tests/vibe/test_mcp_probe.py
git commit -m "fix: validate goal-aware MCP probe events"
```

### Task 3: Full hermetic and opt-in live verification

**Files:**
- Modify after verified runs: `docs/handoff/CURRENT.md`

**Interfaces:**
- Consumes: the running AInvest API at `http://127.0.0.1:8899`, sidecar API at `http://127.0.0.1:8765`, sidecar MCP at `http://127.0.0.1:8766/mcp`, and the operator-installed bearer-authenticated MCP entry.
- Produces: reproducible gate evidence and an updated handoff; no secret values are written.

- [ ] **Step 1: Run the complete hermetic release gate**

Run:

```bash
uv sync --frozen --extra dev
uv lock --check
uv run ruff check src tests
uv run mypy src
uv run pytest -m "not contract" --cov=vibe_portfolio --cov-report=term-missing --cov-fail-under=85
```

Expected: dependency sync/lock, Ruff, and mypy exit `0`; all non-contract tests pass and line coverage is at least 85%.

- [ ] **Step 2: Verify the three local processes and route-only compatibility**

Run read-only health checks for ports `8899`, `8765`, and `8766`, restarting only a stopped process with the previously established isolated AInvest Python 3.11 environment and token file. Then run:

```bash
uv run portfolio-compat-check --contract-only
```

Expected: Vibe version `0.1.11` is route-compatible; no route or method drift is reported.

- [ ] **Step 3: Run the opt-in live runtime gate**

Run with the existing local compatibility API key in the process environment, never printing or writing it:

```bash
PORTFOLIO_RUN_RUNTIME_CONTRACT=1 \
PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:8899 \
PORTFOLIO_VIBE_API_KEY="$VIBE_COMPAT_API_KEY" \
uv run pytest \
  tests/contract/test_live_vibe_contract.py::test_running_vibe_passes_the_public_runtime_contract \
  -q
```

Expected: one test passes and the exact original attempt receives a proven cancellation or terminal-message outcome.

- [ ] **Step 4: Run the opt-in operator-configured MCP gate**

Run:

```bash
PORTFOLIO_RUN_MCP_PROBE=1 \
PORTFOLIO_VIBE_BASE_URL=http://127.0.0.1:8899 \
PORTFOLIO_VIBE_API_KEY="$VIBE_COMPAT_API_KEY" \
uv run pytest \
  tests/contract/test_live_vibe_contract.py::test_operator_configured_portfolio_mcp_probe \
  -q
```

Expected: one test passes; diagnostics show exactly one successful `mcp_portfolio_portfolio_get_capabilities` pair, with any additional observed tool names limited to the three allowed research-goal control tools.

- [ ] **Step 5: Update and validate the handoff**

Update `docs/handoff/CURRENT.md` with the current branch/HEAD, origin state, Task 1 and Task 2 commits, exact test counts/coverage, live gate outcomes, the AInvest lockfile installation caveat, operator config backup location, and the next recommended milestone. Record no bearer tokens, API keys, account IDs, or holdings.

Run:

```bash
rg -n "token|api.key|secret|holding|account" docs/handoff/CURRENT.md
git diff --check
git status --short --branch
```

Expected: any matches are generic safety statements or redacted file paths only; the diff has no whitespace errors; only intended repository changes remain.

- [ ] **Step 6: Commit the verified handoff**

```bash
git add docs/handoff/CURRENT.md
git commit -m "docs: record live compatibility verification"
```

- [ ] **Step 7: Run final verification before reporting completion**

Run `git status --short --branch`, `git log --oneline -6`, and the focused runtime/MCP unit tests once more. Report a gate as passed only from these fresh command results; report any skipped opt-in layer as skipped rather than passed.
