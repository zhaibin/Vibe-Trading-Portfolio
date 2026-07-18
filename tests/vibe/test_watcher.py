import asyncio
from collections.abc import AsyncIterator

import pytest

from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.models import MessageRecord
from vibe_portfolio.vibe.sse import SseEvent, iter_sse
from vibe_portfolio.vibe.watcher import AttemptStatus, AttemptWatcher


async def as_lines(values: list[str]) -> AsyncIterator[str]:
    for value in values:
        yield value


async def no_sleep(_: float) -> None:
    return None


async def test_parser_supports_ids_events_and_multiline_json() -> None:
    events = [
        event
        async for event in iter_sse(
            as_lines(["id: e1", "event: tool_call", 'data: {"tool":', 'data: "portfolio"}', ""])
        )
    ]

    assert events == [SseEvent(event_id="e1", event_type="tool_call", data={"tool": "portfolio"})]


async def test_parser_ignores_comments_and_dispatches_final_unterminated_frame() -> None:
    events = [
        event
        async for event in iter_sse(
            as_lines([": keep-alive", "id: e1", "data: 1", "", "event: update", "data: [1, 2]"])
        )
    ]

    assert events == [
        SseEvent(event_id="e1", event_type="message", data={"value": 1}),
        SseEvent(event_id="e1", event_type="update", data={"value": [1, 2]}),
    ]


async def test_parser_normalizes_an_empty_event_field_to_message() -> None:
    events = [event async for event in iter_sse(as_lines(["event:", "data: {}", ""]))]

    assert events == [SseEvent(event_id=None, event_type="message", data={})]


class FakeWatchGateway:
    def __init__(
        self,
        streams: list[list[SseEvent] | GatewayError | AsyncIterator[SseEvent]],
        messages: list[MessageRecord] | None = None,
    ) -> None:
        self.streams = streams
        self.messages = messages or []
        self.stream_calls: list[tuple[str, str | None]] = []
        self.poll_calls = 0

    async def stream_events(self, session_id: str, last_event_id: str | None = None) -> AsyncIterator[SseEvent]:
        self.stream_calls.append((session_id, last_event_id))
        current = self.streams.pop(0)
        if isinstance(current, GatewayError):
            raise current
        if isinstance(current, list):
            for event in current:
                yield event
            return
        async for event in current:
            yield event

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        self.poll_calls += 1
        return self.messages


async def test_reconnects_same_session_from_last_event_id_without_polling() -> None:
    gateway = FakeWatchGateway(
        streams=[
            [SseEvent(event_id="e1", event_type="tool_call", data={"tool": "probe"})],
            [SseEvent(event_id="e2", event_type="attempt.completed", data={"attempt_id": "attempt-1"})],
        ]
    )
    watcher = AttemptWatcher(gateway, max_reconnects=2, poll_interval_seconds=0, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.COMPLETED
    assert gateway.stream_calls == [("session-1", None), ("session-1", "e1")]
    assert gateway.poll_calls == 0
    assert outcome.used_polling is False


async def test_reconnect_preserves_an_empty_event_id_cursor_reset() -> None:
    gateway = FakeWatchGateway(
        streams=[
            [SseEvent(event_id="", event_type="tool_call", data={"tool": "probe"})],
            [SseEvent(event_id="e2", event_type="attempt.completed", data={"attempt_id": "attempt-1"})],
        ]
    )
    watcher = AttemptWatcher(gateway, max_reconnects=1, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.COMPLETED
    assert gateway.stream_calls == [("session-1", None), ("session-1", "")]


async def test_terminal_event_for_another_attempt_is_ignored() -> None:
    assistant = message_for_attempt("attempt-1")
    gateway = FakeWatchGateway(
        streams=[
            [SseEvent(event_id="e1", event_type="attempt.completed", data={"attempt_id": "attempt-other"})]
        ],
        messages=[assistant],
    )
    watcher = AttemptWatcher(gateway, max_reconnects=0, poll_interval_seconds=0, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.COMPLETED
    assert outcome.terminal_event is None
    assert outcome.assistant_message == assistant
    assert outcome.used_polling is True


async def test_falls_back_to_polling_the_original_attempt() -> None:
    assistant = message_for_attempt("attempt-1")
    gateway = FakeWatchGateway(
        streams=[GatewayError(GatewayErrorCode.VIBE_UNAVAILABLE, "stream lost")],
        messages=[message_for_attempt("attempt-other"), assistant],
    )
    watcher = AttemptWatcher(gateway, max_reconnects=0, poll_interval_seconds=0, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.COMPLETED
    assert outcome.used_polling is True
    assert outcome.assistant_message == assistant
    assert gateway.stream_calls == [("session-1", None)]


async def test_polling_flag_is_false_when_deadline_expires_before_a_poll() -> None:
    monotonic_calls = 0

    def monotonic() -> float:
        nonlocal monotonic_calls
        monotonic_calls += 1
        return 0.0 if monotonic_calls < 3 else 2.0

    gateway = FakeWatchGateway(streams=[[]])
    watcher = AttemptWatcher(
        gateway,
        max_reconnects=0,
        timeout_seconds=1,
        sleep=no_sleep,
        monotonic=monotonic,
    )

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.TIMED_OUT
    assert outcome.used_polling is False
    assert gateway.poll_calls == 0


async def test_stream_errors_exhaust_retries_with_bounded_backoff_before_polling() -> None:
    sleep_delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    gateway = FakeWatchGateway(
        streams=[
            GatewayError(GatewayErrorCode.VIBE_UNAVAILABLE, "stream lost 1"),
            GatewayError(GatewayErrorCode.VIBE_TIMEOUT, "stream lost 2"),
            GatewayError(GatewayErrorCode.VIBE_UPSTREAM_ERROR, "stream lost 3"),
        ],
        messages=[message_for_attempt("attempt-1")],
    )
    watcher = AttemptWatcher(
        gateway,
        max_reconnects=2,
        poll_interval_seconds=1,
        timeout_seconds=10,
        sleep=record_sleep,
        monotonic=lambda: 0.0,
    )

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.COMPLETED
    assert outcome.used_polling is True
    assert gateway.stream_calls == [
        ("session-1", None),
        ("session-1", None),
        ("session-1", None),
    ]
    assert sleep_delays == [0.25, 0.5]
    assert gateway.poll_calls == 1


@pytest.mark.parametrize(
    ("message_status", "expected"),
    [("failed", AttemptStatus.FAILED), ("cancelled", AttemptStatus.CANCELLED)],
)
async def test_polling_preserves_non_success_terminal_status(
    message_status: str, expected: AttemptStatus
) -> None:
    gateway = FakeWatchGateway(
        streams=[GatewayError(GatewayErrorCode.VIBE_UNAVAILABLE, "stream lost")],
        messages=[message_for_attempt("attempt-1", status=message_status)],
    )
    watcher = AttemptWatcher(gateway, max_reconnects=0, poll_interval_seconds=0, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is expected
    assert outcome.used_polling is True


async def test_failed_terminal_event_is_not_reported_as_success() -> None:
    gateway = FakeWatchGateway(
        streams=[
            [
                SseEvent(
                    event_id="e1",
                    event_type="attempt.failed",
                    data={"attempt_id": "attempt-1", "error": "provider"},
                )
            ]
        ]
    )
    watcher = AttemptWatcher(gateway, max_reconnects=0, timeout_seconds=10, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.FAILED
    assert outcome.terminal_event is not None
    assert outcome.terminal_event.data["error"] == "provider"


async def test_overall_timeout_interrupts_a_stalled_stream() -> None:
    async def stalled_stream() -> AsyncIterator[SseEvent]:
        await asyncio.Event().wait()
        yield SseEvent(event_id=None, event_type="unreachable", data={})

    gateway = FakeWatchGateway(streams=[stalled_stream()])
    watcher = AttemptWatcher(gateway, max_reconnects=0, timeout_seconds=0.01, sleep=no_sleep)

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.TIMED_OUT
    assert outcome.used_polling is False
    assert gateway.poll_calls == 0


async def test_terminal_event_after_deadline_is_not_accepted() -> None:
    monotonic_calls = 0

    def monotonic() -> float:
        nonlocal monotonic_calls
        monotonic_calls += 1
        return 0.0 if monotonic_calls < 3 else 2.0

    gateway = FakeWatchGateway(
        streams=[
            [SseEvent(event_id="e1", event_type="attempt.completed", data={"attempt_id": "attempt-1"})]
        ]
    )
    watcher = AttemptWatcher(
        gateway,
        max_reconnects=0,
        timeout_seconds=1,
        sleep=no_sleep,
        monotonic=monotonic,
    )

    outcome = await watcher.wait("session-1", "attempt-1")

    assert outcome.status is AttemptStatus.TIMED_OUT
    assert outcome.terminal_event is None


def message_for_attempt(attempt_id: str, *, status: str = "completed") -> MessageRecord:
    return MessageRecord(
        message_id=f"message-{attempt_id}",
        session_id="session-1",
        role="assistant",
        content="done",
        created_at="2026-07-18T00:00:01Z",
        linked_attempt_id=attempt_id,
        metadata={"status": status},
    )
