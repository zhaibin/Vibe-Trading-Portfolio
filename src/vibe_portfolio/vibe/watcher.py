import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from vibe_portfolio.vibe.errors import GatewayError
from vibe_portfolio.vibe.models import MessageRecord
from vibe_portfolio.vibe.sse import SseEvent


class AttemptStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class WatchGateway(Protocol):
    def stream_events(self, session_id: str, last_event_id: str | None = None) -> AsyncIterator[SseEvent]:
        ...

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        ...


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    session_id: str
    attempt_id: str
    status: AttemptStatus
    events: tuple[SseEvent, ...]
    terminal_event: SseEvent | None
    assistant_message: MessageRecord | None
    used_polling: bool


class AttemptWatcher:
    def __init__(
        self,
        gateway: WatchGateway,
        *,
        max_reconnects: int = 2,
        poll_interval_seconds: float = 1.0,
        timeout_seconds: float = 300.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gateway = gateway
        self.max_reconnects = max_reconnects
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.monotonic = monotonic

    async def wait(self, session_id: str, attempt_id: str) -> AttemptOutcome:
        deadline = self.monotonic() + self.timeout_seconds
        events: list[SseEvent] = []
        last_event_id: str | None = None
        used_polling = False

        async def observe() -> AttemptOutcome:
            nonlocal last_event_id, used_polling

            for reconnect_index in range(self.max_reconnects + 1):
                if self.monotonic() >= deadline:
                    break
                try:
                    async for event in self.gateway.stream_events(session_id, last_event_id):
                        if self.monotonic() >= deadline:
                            break
                        events.append(event)
                        if event.event_id is not None:
                            last_event_id = event.event_id
                        terminal = self._terminal_status(event, attempt_id)
                        if terminal is not None:
                            return AttemptOutcome(
                                session_id, attempt_id, terminal, tuple(events), event, None, False
                            )
                except GatewayError:
                    pass
                if reconnect_index < self.max_reconnects and self.monotonic() < deadline:
                    await self.sleep(min(0.25 * (2**reconnect_index), 2.0))

            while self.monotonic() < deadline:
                used_polling = True
                for message in await self.gateway.list_messages(session_id, limit=100):
                    if message.role == "assistant" and message.linked_attempt_id == attempt_id:
                        status = {
                            "completed": AttemptStatus.COMPLETED,
                            "failed": AttemptStatus.FAILED,
                            "cancelled": AttemptStatus.CANCELLED,
                        }.get(str((message.metadata or {}).get("status")), AttemptStatus.FAILED)
                        return AttemptOutcome(
                            session_id, attempt_id, status, tuple(events), None, message, True
                        )
                await self.sleep(self.poll_interval_seconds)

            return self._timed_out(session_id, attempt_id, events, used_polling=used_polling)

        try:
            async with asyncio.timeout(max(self.timeout_seconds, 0)):
                return await observe()
        except TimeoutError:
            return self._timed_out(session_id, attempt_id, events, used_polling=used_polling)

    @staticmethod
    def _terminal_status(event: SseEvent, attempt_id: str) -> AttemptStatus | None:
        if event.data.get("attempt_id") != attempt_id:
            return None
        return {
            "attempt.completed": AttemptStatus.COMPLETED,
            "attempt.failed": AttemptStatus.FAILED,
            "attempt.cancelled": AttemptStatus.CANCELLED,
        }.get(event.event_type)

    @staticmethod
    def _timed_out(
        session_id: str, attempt_id: str, events: list[SseEvent], *, used_polling: bool
    ) -> AttemptOutcome:
        return AttemptOutcome(
            session_id,
            attempt_id,
            AttemptStatus.TIMED_OUT,
            tuple(events),
            None,
            None,
            used_polling,
        )
