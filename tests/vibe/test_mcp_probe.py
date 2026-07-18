from vibe_portfolio.compatibility import McpStatus
from vibe_portfolio.vibe.mcp_probe import EXPECTED_VIBE_TOOL_NAME, PortfolioMcpProbe
from vibe_portfolio.vibe.models import CancelResult, GoalSnapshot, MessageAccepted, SessionRecord
from vibe_portfolio.vibe.sse import SseEvent
from vibe_portfolio.vibe.watcher import AttemptOutcome, AttemptStatus


class FakeGateway:
    def __init__(self) -> None:
        self.session_titles: list[str] = []
        self.goal_payloads: list[tuple[str, str, list[str]]] = []
        self.messages: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    async def create_session(self, title: str) -> SessionRecord:
        self.session_titles.append(title)
        return SessionRecord(
            session_id="session-1",
            title=title,
            status="active",
            created_at="2026-07-18T00:00:00Z",
            updated_at="2026-07-18T00:00:00Z",
        )

    async def create_research_goal(
        self, session_id: str, objective: str, criteria: list[str]
    ) -> GoalSnapshot:
        self.goal_payloads.append((session_id, objective, criteria))
        return GoalSnapshot(goal={"goal_id": "goal-1"})

    async def send_message(self, session_id: str, content: str) -> MessageAccepted:
        self.messages.append((session_id, content))
        return MessageAccepted(message_id="message-1", attempt_id="attempt-1")

    async def cancel(self, session_id: str) -> CancelResult:
        self.cancelled.append(session_id)
        return CancelResult(status="cancelled")


class FakeWatcher:
    def __init__(self, outcome: AttemptOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    async def wait(self, session_id: str, attempt_id: str) -> AttemptOutcome:
        self.calls.append((session_id, attempt_id))
        return self.outcome


def outcome_with(
    *events: SseEvent,
    status: AttemptStatus = AttemptStatus.COMPLETED,
    session_id: str = "session-1",
    attempt_id: str = "attempt-1",
) -> AttemptOutcome:
    terminal = next((event for event in events if event.event_type.startswith("attempt.")), None)
    return AttemptOutcome(
        session_id, attempt_id, status, tuple(events), terminal, None, False
    )


async def test_probe_requires_observed_successful_tool_call_and_result() -> None:
    events = (
        SseEvent(
            "e1",
            "tool_call",
            {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME},
        ),
        SseEvent(
            "e2",
            "tool_result",
            {
                "attempt_id": "attempt-1",
                "tool": EXPECTED_VIBE_TOOL_NAME,
                "status": "ok",
            },
        ),
        SseEvent("e3", "attempt.completed", {"attempt_id": "attempt-1"}),
    )
    gateway = FakeGateway()
    watcher = FakeWatcher(outcome_with(*events))

    result = await PortfolioMcpProbe(gateway, watcher).run()

    assert result.status is McpStatus.AVAILABLE
    assert result.observed_tools == [EXPECTED_VIBE_TOOL_NAME]
    assert watcher.calls == [("session-1", "attempt-1")]
    assert "Do not place orders" in gateway.messages[0][1]
    assert "broker-write" in gateway.messages[0][1]
    assert "execute trades" in gateway.messages[0][1]
    assert "mcpServers" not in gateway.messages[0][1]


async def test_completed_run_without_tool_event_is_missing_not_success() -> None:
    gateway = FakeGateway()
    watcher = FakeWatcher(
        outcome_with(SseEvent("e1", "attempt.completed", {"attempt_id": "attempt-1"}))
    )

    result = await PortfolioMcpProbe(gateway, watcher).run()

    assert result.status is McpStatus.MISSING
    assert result.reason == "expected_tool_call_not_observed"


async def test_timed_out_probe_cancels_original_session() -> None:
    gateway = FakeGateway()
    watcher = FakeWatcher(outcome_with(status=AttemptStatus.TIMED_OUT))

    result = await PortfolioMcpProbe(gateway, watcher).run()

    assert result.status is McpStatus.FAILED
    assert result.reason == "probe_timed_out"
    assert gateway.cancelled == ["session-1"]


async def test_tool_events_from_another_attempt_do_not_prove_availability() -> None:
    events = (
        SseEvent(
            "e1",
            "tool_call",
            {"attempt_id": "attempt-other", "tool": EXPECTED_VIBE_TOOL_NAME},
        ),
        SseEvent(
            "e2",
            "tool_result",
            {
                "attempt_id": "attempt-other",
                "tool": EXPECTED_VIBE_TOOL_NAME,
                "status": "ok",
            },
        ),
        SseEvent("e3", "attempt.completed", {"attempt_id": "attempt-1"}),
    )

    result = await PortfolioMcpProbe(FakeGateway(), FakeWatcher(outcome_with(*events))).run()

    assert result.status is McpStatus.MISSING
    assert result.reason == "expected_tool_call_not_observed"


async def test_failed_attempt_cannot_be_available_even_with_success_events() -> None:
    events = (
        SseEvent(
            "e1",
            "tool_call",
            {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME},
        ),
        SseEvent(
            "e2",
            "tool_result",
            {
                "attempt_id": "attempt-1",
                "tool": EXPECTED_VIBE_TOOL_NAME,
                "status": "ok",
            },
        ),
        SseEvent("e3", "attempt.failed", {"attempt_id": "attempt-1"}),
    )

    result = await PortfolioMcpProbe(
        FakeGateway(), FakeWatcher(outcome_with(*events, status=AttemptStatus.FAILED))
    ).run()

    assert result.status is McpStatus.FAILED
    assert result.reason == "probe_attempt_failed"


async def test_unexpected_or_duplicate_tool_call_fails_closed() -> None:
    for extra_tool in ("some_other_tool", EXPECTED_VIBE_TOOL_NAME):
        events = (
            SseEvent(
                "e1",
                "tool_call",
                {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME},
            ),
            SseEvent(
                "e2",
                "tool_call",
                {"attempt_id": "attempt-1", "tool": extra_tool},
            ),
            SseEvent(
                "e3",
                "tool_result",
                {
                    "attempt_id": "attempt-1",
                    "tool": EXPECTED_VIBE_TOOL_NAME,
                    "status": "ok",
                },
            ),
            SseEvent("e4", "attempt.completed", {"attempt_id": "attempt-1"}),
        )

        result = await PortfolioMcpProbe(
            FakeGateway(), FakeWatcher(outcome_with(*events))
        ).run()

        assert result.status is McpStatus.FAILED
        assert result.reason == "unexpected_tool_calls_observed"


async def test_tool_call_without_successful_result_fails_closed() -> None:
    events = (
        SseEvent(
            "e1",
            "tool_call",
            {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME},
        ),
        SseEvent(
            "e2",
            "tool_result",
            {
                "attempt_id": "attempt-1",
                "tool": EXPECTED_VIBE_TOOL_NAME,
                "status": "error",
            },
        ),
        SseEvent("e3", "attempt.completed", {"attempt_id": "attempt-1"}),
    )

    result = await PortfolioMcpProbe(FakeGateway(), FakeWatcher(outcome_with(*events))).run()

    assert result.status is McpStatus.FAILED
    assert result.reason == "tool_result_not_successful"


async def test_non_unique_or_unexpected_tool_results_fail_closed() -> None:
    invalid_results = (
        (
            SseEvent(
                "e2",
                "tool_result",
                {
                    "attempt_id": "attempt-1",
                    "tool": EXPECTED_VIBE_TOOL_NAME,
                    "status": "ok",
                },
            ),
            SseEvent(
                "e3",
                "tool_result",
                {
                    "attempt_id": "attempt-1",
                    "tool": EXPECTED_VIBE_TOOL_NAME,
                    "status": "ok",
                },
            ),
        ),
        (
            SseEvent(
                "e2",
                "tool_result",
                {
                    "attempt_id": "attempt-1",
                    "tool": EXPECTED_VIBE_TOOL_NAME,
                    "status": "error",
                },
            ),
            SseEvent(
                "e3",
                "tool_result",
                {
                    "attempt_id": "attempt-1",
                    "tool": EXPECTED_VIBE_TOOL_NAME,
                    "status": "ok",
                },
            ),
        ),
        (
            SseEvent(
                "e2",
                "tool_result",
                {
                    "attempt_id": "attempt-1",
                    "tool": EXPECTED_VIBE_TOOL_NAME,
                    "status": "ok",
                },
            ),
            SseEvent(
                "e3",
                "tool_result",
                {
                    "attempt_id": "attempt-1",
                    "tool": "some_other_tool",
                    "status": "ok",
                },
            ),
        ),
    )

    for tool_results in invalid_results:
        events = (
            SseEvent(
                "e1",
                "tool_call",
                {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME},
            ),
            *tool_results,
            SseEvent("e4", "attempt.completed", {"attempt_id": "attempt-1"}),
        )

        result = await PortfolioMcpProbe(
            FakeGateway(), FakeWatcher(outcome_with(*events))
        ).run()

        assert result.status is McpStatus.FAILED
        assert result.reason == "unexpected_tool_results_observed"


async def test_tool_result_before_the_unique_call_fails_closed() -> None:
    events = (
        SseEvent(
            "e1",
            "tool_result",
            {
                "attempt_id": "attempt-1",
                "tool": EXPECTED_VIBE_TOOL_NAME,
                "status": "ok",
            },
        ),
        SseEvent(
            "e2",
            "tool_call",
            {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME},
        ),
        SseEvent("e3", "attempt.completed", {"attempt_id": "attempt-1"}),
    )

    result = await PortfolioMcpProbe(FakeGateway(), FakeWatcher(outcome_with(*events))).run()

    assert result.status is McpStatus.FAILED
    assert result.reason == "tool_result_not_after_call"


async def test_watcher_outcome_session_mismatch_fails_closed() -> None:
    events = (
        SseEvent(
            "e1",
            "tool_call",
            {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME},
        ),
        SseEvent(
            "e2",
            "tool_result",
            {
                "attempt_id": "attempt-1",
                "tool": EXPECTED_VIBE_TOOL_NAME,
                "status": "ok",
            },
        ),
        SseEvent("e3", "attempt.completed", {"attempt_id": "attempt-1"}),
    )
    outcome = outcome_with(*events, session_id="session-other")

    result = await PortfolioMcpProbe(FakeGateway(), FakeWatcher(outcome)).run()

    assert result.status is McpStatus.FAILED
    assert result.reason == "probe_outcome_session_mismatch"


async def test_watcher_outcome_attempt_mismatch_fails_closed() -> None:
    events = (
        SseEvent(
            "e1",
            "tool_call",
            {"attempt_id": "attempt-1", "tool": EXPECTED_VIBE_TOOL_NAME},
        ),
        SseEvent(
            "e2",
            "tool_result",
            {
                "attempt_id": "attempt-1",
                "tool": EXPECTED_VIBE_TOOL_NAME,
                "status": "ok",
            },
        ),
        SseEvent("e3", "attempt.completed", {"attempt_id": "attempt-1"}),
    )
    outcome = outcome_with(*events, attempt_id="attempt-other")

    result = await PortfolioMcpProbe(FakeGateway(), FakeWatcher(outcome)).run()

    assert result.status is McpStatus.FAILED
    assert result.reason == "probe_outcome_attempt_mismatch"


class MissingGoalGateway(FakeGateway):
    async def create_research_goal(
        self, session_id: str, objective: str, criteria: list[str]
    ) -> GoalSnapshot:
        return GoalSnapshot(goal={})


class NullGoalGateway(FakeGateway):
    async def create_research_goal(
        self, session_id: str, objective: str, criteria: list[str]
    ) -> GoalSnapshot:
        return GoalSnapshot(goal={"goal_id": None})


async def test_invalid_goal_id_stops_before_message_submission() -> None:
    for gateway in (MissingGoalGateway(), NullGoalGateway()):
        try:
            await PortfolioMcpProbe(gateway, FakeWatcher(outcome_with())).run()
        except ValueError as error:
            assert str(error) == "Vibe research goal response did not contain goal_id"
        else:
            raise AssertionError("Expected invalid goal_id to fail closed")

        assert gateway.messages == []
