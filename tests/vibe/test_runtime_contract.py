from collections.abc import AsyncIterator

import pytest

from vibe_portfolio.compatibility import (
    AnalysisMode,
    CompatibilityReport,
    CompatibilityState,
    McpStatus,
)
from vibe_portfolio.vibe.contract import RuntimeContractVerifier, gate_exit_code, mcp_gate_exit_code
from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.mcp_probe import McpProbeResult
from vibe_portfolio.vibe.models import (
    CancelResult,
    GoalSnapshot,
    HealthStatus,
    MessageAccepted,
    MessageRecord,
    ProbeResult,
    SessionRecord,
    SseTicket,
)
from vibe_portfolio.vibe.sse import SseEvent


class FakeDiscovery:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready

    async def discover(self, mcp_status: McpStatus) -> CompatibilityReport:
        assert mcp_status is McpStatus.NOT_CHECKED
        return CompatibilityReport(
            state=CompatibilityState.DEGRADED,
            analysis_mode=AnalysisMode.BOUNDED_CONTEXT if self.ready else AnalysisMode.DISABLED,
            contract_compatible=True,
            deep_analysis_enabled=self.ready,
            vibe_version="0.1.11",
            mcp_status=mcp_status,
            reasons=[] if self.ready else ["vibe_not_ready"],
        )


class FakeRuntimeGateway:
    def __init__(self, *, fail_at: str | None = None, ready: bool = True) -> None:
        self.fail_at = fail_at
        self.is_ready = ready
        self.cancel_calls: list[str] = []
        self.stream_calls: list[tuple[str, str | None]] = []

    def _fail(self, stage: str) -> None:
        if self.fail_at == stage:
            raise GatewayError(GatewayErrorCode.VIBE_CONTRACT_ERROR, f"{stage} drift")

    async def health(self) -> HealthStatus:
        self._fail("health")
        return HealthStatus(status="healthy", service="Vibe-Trading API", timestamp="2026-07-18T00:00:00Z")

    async def ready(self) -> ProbeResult:
        self._fail("ready")
        return ProbeResult(ok=self.is_ready, status_code=200 if self.is_ready else 503)

    async def create_session(self, title: str) -> SessionRecord:
        self._fail("session")
        return SessionRecord(
            session_id="session-1",
            title=title,
            status="active",
            created_at="2026-07-18T00:00:00Z",
            updated_at="2026-07-18T00:00:00Z",
        )

    async def create_research_goal(self, session_id: str, objective: str, criteria: list[str]) -> GoalSnapshot:
        self._fail("goal")
        return GoalSnapshot(goal={"goal_id": "goal-1"})

    async def mint_sse_ticket(self) -> SseTicket:
        self._fail("ticket")
        return SseTicket(ticket="ticket-1")

    async def send_message(self, session_id: str, content: str) -> MessageAccepted:
        self._fail("message")
        return MessageAccepted(message_id="message-1", attempt_id="attempt-1")

    async def stream_events(
        self, session_id: str, last_event_id: str | None = None
    ) -> AsyncIterator[SseEvent]:
        self.stream_calls.append((session_id, last_event_id))
        self._fail("sse")
        if last_event_id is None:
            yield SseEvent(
                "event-message",
                "message.received",
                {"message_id": "message-1", "role": "user"},
            )
            yield SseEvent("event-created", "attempt.created", {"attempt_id": "attempt-1"})
            yield SseEvent("event-started", "attempt.started", {"attempt_id": "attempt-1"})
        else:
            yield SseEvent("event-started", "attempt.started", {"attempt_id": "attempt-1"})

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        self._fail("poll")
        return [
            MessageRecord(
                message_id="message-1",
                session_id=session_id,
                role="user",
                content="runtime probe",
                created_at="2026-07-18T00:00:01Z",
            )
        ]

    async def cancel(self, session_id: str) -> CancelResult:
        self.cancel_calls.append(session_id)
        self._fail("cancel")
        return CancelResult(status="cancelled")


class ArbitraryEventRuntimeGateway(FakeRuntimeGateway):
    async def stream_events(
        self, session_id: str, last_event_id: str | None = None
    ) -> AsyncIterator[SseEvent]:
        self.stream_calls.append((session_id, last_event_id))
        if last_event_id is None:
            yield SseEvent("unrelated-1", "tool_call", {"attempt_id": "attempt-other"})
        else:
            yield SseEvent("unrelated-2", "text_delta", {"attempt_id": "attempt-other"})


class UnprovenNoActiveLoopGateway(FakeRuntimeGateway):
    async def cancel(self, session_id: str) -> CancelResult:
        self.cancel_calls.append(session_id)
        return CancelResult(status="no_active_loop")


class TerminalNoActiveLoopGateway(UnprovenNoActiveLoopGateway):
    def __init__(self) -> None:
        super().__init__()
        self.poll_calls = 0

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        self.poll_calls += 1
        messages = await super().list_messages(session_id, limit)
        if self.poll_calls > 1:
            messages.append(
                MessageRecord(
                    message_id="assistant-1",
                    session_id=session_id,
                    role="assistant",
                    content="done",
                    created_at="2026-07-18T00:00:02Z",
                    linked_attempt_id="attempt-1",
                    metadata={"status": "completed"},
                )
            )
        return messages


async def no_sleep(_: float) -> None:
    return None


async def test_runtime_contract_exercises_every_public_runtime_path() -> None:
    gateway = FakeRuntimeGateway()

    result = await RuntimeContractVerifier(
        gateway,
        FakeDiscovery(),
        stream_warmup_seconds=0,
        event_timeout_seconds=1,
        sleep=no_sleep,
    ).verify()

    assert result.passed is True
    assert result.stage == "complete"
    assert result.session_id == "session-1"
    assert result.attempt_id == "attempt-1"
    assert gateway.stream_calls == [("session-1", None), ("session-1", "event-created")]
    assert gateway.cancel_calls == ["session-1"]
    assert gate_exit_code(result) == 0


async def test_arbitrary_distinct_sse_events_cannot_satisfy_attempt_identity_or_replay() -> None:
    result = await RuntimeContractVerifier(
        ArbitraryEventRuntimeGateway(),
        FakeDiscovery(),
        stream_warmup_seconds=0,
        event_timeout_seconds=0.05,
        sleep=no_sleep,
    ).verify()

    assert result.passed is False
    assert result.stage in {"sse", "sse_replay"}


async def test_no_active_loop_requires_exact_terminal_proof_for_original_attempt() -> None:
    result = await RuntimeContractVerifier(
        UnprovenNoActiveLoopGateway(),
        FakeDiscovery(),
        stream_warmup_seconds=0,
        event_timeout_seconds=0.05,
        sleep=no_sleep,
    ).verify()

    assert result.passed is False
    assert result.stage == "cancel"
    assert result.reason == "cancel_not_proven_for_attempt"


async def test_no_active_loop_passes_with_exact_terminal_proof_for_original_attempt() -> None:
    gateway = TerminalNoActiveLoopGateway()

    result = await RuntimeContractVerifier(
        gateway,
        FakeDiscovery(),
        stream_warmup_seconds=0,
        event_timeout_seconds=0.05,
        sleep=no_sleep,
    ).verify()

    assert result.passed is True
    assert gateway.cancel_calls == ["session-1"]
    assert gateway.poll_calls == 2


@pytest.mark.parametrize("fail_at", ["session", "goal", "message", "ticket", "sse", "poll", "cancel"])
async def test_runtime_dto_ticket_sse_poll_and_cancel_drift_fail_the_gate(fail_at: str) -> None:
    result = await RuntimeContractVerifier(
        FakeRuntimeGateway(fail_at=fail_at),
        FakeDiscovery(),
        stream_warmup_seconds=0,
        event_timeout_seconds=0.05,
        sleep=no_sleep,
    ).verify()

    assert result.passed is False
    assert gate_exit_code(result) == 2


async def test_ready_503_fails_the_runtime_gate_even_when_routes_are_compatible() -> None:
    result = await RuntimeContractVerifier(
        FakeRuntimeGateway(ready=False),
        FakeDiscovery(ready=False),
        stream_warmup_seconds=0,
        event_timeout_seconds=0.05,
        sleep=no_sleep,
    ).verify()

    assert result.passed is False
    assert result.stage == "ready"
    assert gate_exit_code(result) == 2


@pytest.mark.parametrize("status", [McpStatus.NOT_CHECKED, McpStatus.MISSING, McpStatus.FAILED])
def test_unavailable_or_drifted_mcp_probe_is_nonzero(status: McpStatus) -> None:
    result = McpProbeResult(status, "session-1", "attempt-1", [], "not_available")

    assert mcp_gate_exit_code(result) == 2


def test_only_available_mcp_probe_is_zero() -> None:
    result = McpProbeResult(McpStatus.AVAILABLE, "session-1", "attempt-1", ["expected"])

    assert mcp_gate_exit_code(result) == 0
