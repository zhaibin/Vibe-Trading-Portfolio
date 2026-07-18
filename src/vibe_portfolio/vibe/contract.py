import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol, TypeVar

from pydantic import BaseModel

from vibe_portfolio.compatibility import CompatibilityReport, McpStatus
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

AwaitedT = TypeVar("AwaitedT")


class RuntimeDiscovery(Protocol):
    async def discover(self, mcp_status: McpStatus) -> CompatibilityReport: ...


class RuntimeGateway(Protocol):
    async def health(self) -> HealthStatus: ...

    async def ready(self) -> ProbeResult: ...

    async def create_session(self, title: str) -> SessionRecord: ...

    async def create_research_goal(
        self, session_id: str, objective: str, criteria: list[str]
    ) -> GoalSnapshot: ...

    async def mint_sse_ticket(self) -> SseTicket: ...

    async def send_message(self, session_id: str, content: str) -> MessageAccepted: ...

    def stream_events(
        self, session_id: str, last_event_id: str | None = None
    ) -> AsyncIterator[SseEvent]: ...

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]: ...

    async def cancel(self, session_id: str) -> CancelResult: ...


class RuntimeContractResult(BaseModel):
    layer: str = "runtime"
    passed: bool
    stage: str
    reason: str | None = None
    vibe_version: str | None = None
    session_id: str | None = None
    attempt_id: str | None = None
    first_event_id: str | None = None
    replay_event_id: str | None = None


def gate_exit_code(result: RuntimeContractResult) -> int:
    return 0 if result.passed else 2


def mcp_gate_exit_code(result: McpProbeResult) -> int:
    return 0 if result.status is McpStatus.AVAILABLE else 2


class RuntimeContractVerifier:
    """Exercise the supported Vibe public runtime without accepting route-only success."""

    def __init__(
        self,
        gateway: RuntimeGateway,
        discovery: RuntimeDiscovery,
        *,
        stream_warmup_seconds: float = 0.25,
        event_timeout_seconds: float = 10.0,
        terminal_poll_interval_seconds: float = 0.25,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not math.isfinite(terminal_poll_interval_seconds) or terminal_poll_interval_seconds <= 0:
            raise ValueError("terminal_poll_interval_seconds must be positive")
        self.gateway = gateway
        self.discovery = discovery
        self.stream_warmup_seconds = stream_warmup_seconds
        self.event_timeout_seconds = event_timeout_seconds
        self.terminal_poll_interval_seconds = terminal_poll_interval_seconds
        self.sleep = sleep

    async def verify(self) -> RuntimeContractResult:
        stage = "route_only"
        version: str | None = None
        session_id: str | None = None
        attempt_id: str | None = None
        stream_task: asyncio.Task[tuple[SseEvent, SseEvent, SseEvent]] | None = None
        cancel_attempted = False
        try:
            report = await self._bounded(self.discovery.discover(McpStatus.NOT_CHECKED))
            version = report.vibe_version
            if not report.contract_compatible:
                return self._failed(stage, "route_contract_incompatible", version=version)

            stage = "health"
            health = await self._bounded(self.gateway.health())
            if health.status != "healthy":
                return self._failed(stage, "health_not_healthy", version=version)

            stage = "ready"
            readiness = await self._bounded(self.gateway.ready())
            if not readiness.ok:
                return self._failed(stage, "vibe_not_ready", version=version)

            stage = "session"
            session = await self._bounded(
                self.gateway.create_session("Portfolio public runtime contract probe")
            )
            session_id = session.session_id

            stage = "goal"
            goal = await self._bounded(
                self.gateway.create_research_goal(
                    session_id,
                    "Verify the public research-only Vibe runtime contract",
                    ["Exercise only public runtime endpoints", "Perform no trading or broker writes"],
                )
            )
            goal_id = goal.goal.get("goal_id")
            if not isinstance(goal_id, str) or not goal_id.strip():
                return self._failed(stage, "goal_dto_incompatible", version=version, session_id=session_id)

            stage = "ticket"
            ticket = await self._bounded(self.gateway.mint_sse_ticket())
            if not ticket.ticket.strip():
                return self._failed(stage, "ticket_dto_incompatible", version=version, session_id=session_id)

            stage = "sse"
            identity: asyncio.Future[tuple[str, str]] = asyncio.get_running_loop().create_future()
            stream_task = asyncio.create_task(self._observe_attempt_sequence(session_id, identity))
            await self._bounded(self.sleep(self.stream_warmup_seconds))

            stage = "message"
            accepted = await self._bounded(
                self.gateway.send_message(
                    session_id,
                    "Runtime contract probe only. Return no investment advice and perform no external action.",
                )
            )
            attempt_id = accepted.attempt_id
            identity.set_result((accepted.message_id, accepted.attempt_id))

            stage = "sse"
            _message_event, created_event, started_event = await asyncio.wait_for(
                stream_task, timeout=self.event_timeout_seconds
            )
            stream_task = None

            stage = "sse_replay"
            replay_event = await asyncio.wait_for(
                self._next_event(session_id, created_event.event_id),
                timeout=self.event_timeout_seconds,
            )
            if (
                replay_event.event_id != started_event.event_id
                or replay_event.event_type != "attempt.started"
                or replay_event.data.get("attempt_id") != attempt_id
            ):
                return self._failed(
                    stage,
                    "sse_replay_incompatible",
                    version=version,
                    session_id=session_id,
                    attempt_id=attempt_id,
                )

            stage = "poll"
            messages = await self._bounded(self.gateway.list_messages(session_id, limit=100))
            if not any(
                message.message_id == accepted.message_id
                and message.session_id == session_id
                and message.role == "user"
                for message in messages
            ):
                return self._failed(
                    stage,
                    "message_poll_incompatible",
                    version=version,
                    session_id=session_id,
                    attempt_id=attempt_id,
                )

            stage = "cancel"
            cancel_attempted = True
            cancel_result = await self._bounded(self.gateway.cancel(session_id))
            if cancel_result.status == "no_active_loop":
                if not await self._prove_cancelled_or_terminal(session_id, attempt_id):
                    return self._failed(
                        stage,
                        "cancel_not_proven_for_attempt",
                        version=version,
                        session_id=session_id,
                        attempt_id=attempt_id,
                    )
            elif cancel_result.status != "cancelled":
                return self._failed(
                    stage,
                    "cancel_dto_incompatible",
                    version=version,
                    session_id=session_id,
                    attempt_id=attempt_id,
                )

            return RuntimeContractResult(
                passed=True,
                stage="complete",
                vibe_version=version,
                session_id=session_id,
                attempt_id=attempt_id,
                first_event_id=created_event.event_id,
                replay_event_id=replay_event.event_id,
            )
        except TimeoutError:
            return self._failed(
                stage,
                "runtime_event_timeout",
                version=version,
                session_id=session_id,
                attempt_id=attempt_id,
            )
        except GatewayError as error:
            return self._failed(
                stage,
                error.code.value.lower(),
                version=version,
                session_id=session_id,
                attempt_id=attempt_id,
            )
        except Exception:
            return self._failed(
                stage,
                "runtime_contract_failed",
                version=version,
                session_id=session_id,
                attempt_id=attempt_id,
            )
        finally:
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
            if session_id is not None and not cancel_attempted:
                try:
                    await self._bounded(self.gateway.cancel(session_id))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

    async def _bounded(self, operation: Awaitable[AwaitedT]) -> AwaitedT:
        return await asyncio.wait_for(operation, timeout=max(self.event_timeout_seconds, 0.001))

    async def _observe_attempt_sequence(
        self,
        session_id: str,
        identity: asyncio.Future[tuple[str, str]],
    ) -> tuple[SseEvent, SseEvent, SseEvent]:
        state = "message"
        message_event: SseEvent | None = None
        created_event: SseEvent | None = None
        async for event in self.gateway.stream_events(session_id, None):
            if event.event_type == "heartbeat":
                continue
            message_id, attempt_id = await identity
            if state == "message" and event.event_type == "goal.created":
                continue
            if state == "message":
                if (
                    event.event_type != "message.received"
                    or event.data.get("message_id") != message_id
                    or event.data.get("role") != "user"
                    or not event.event_id
                ):
                    raise GatewayError(
                        GatewayErrorCode.VIBE_CONTRACT_ERROR,
                        "Vibe SSE message identity or sequence is incompatible",
                    )
                message_event = event
                state = "created"
                continue
            if state == "created":
                if (
                    event.event_type != "attempt.created"
                    or event.data.get("attempt_id") != attempt_id
                    or not event.event_id
                ):
                    raise GatewayError(
                        GatewayErrorCode.VIBE_CONTRACT_ERROR,
                        "Vibe SSE attempt creation identity or sequence is incompatible",
                    )
                created_event = event
                state = "started"
                continue
            if (
                event.event_type != "attempt.started"
                or event.data.get("attempt_id") != attempt_id
                or not event.event_id
                or message_event is None
                or created_event is None
            ):
                raise GatewayError(
                    GatewayErrorCode.VIBE_CONTRACT_ERROR,
                    "Vibe SSE attempt start identity or sequence is incompatible",
                )
            return message_event, created_event, event
        raise GatewayError(
            GatewayErrorCode.VIBE_CONTRACT_ERROR,
            "Vibe SSE stream closed before the expected attempt sequence",
        )

    async def _next_event(self, session_id: str, last_event_id: str | None) -> SseEvent:
        async for event in self.gateway.stream_events(session_id, last_event_id):
            return event
        raise GatewayError(GatewayErrorCode.VIBE_CONTRACT_ERROR, "Vibe SSE stream closed without an event")

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

    @staticmethod
    def _has_terminal_attempt_message(
        messages: list[MessageRecord], session_id: str, attempt_id: str
    ) -> bool:
        terminal_statuses = {"completed", "failed", "cancelled"}
        return any(
            message.session_id == session_id
            and message.role == "assistant"
            and message.linked_attempt_id == attempt_id
            and str((message.metadata or {}).get("status")) in terminal_statuses
            for message in messages
        )

    @staticmethod
    def _failed(
        stage: str,
        reason: str,
        *,
        version: str | None = None,
        session_id: str | None = None,
        attempt_id: str | None = None,
    ) -> RuntimeContractResult:
        return RuntimeContractResult(
            passed=False,
            stage=stage,
            reason=reason,
            vibe_version=version,
            session_id=session_id,
            attempt_id=attempt_id,
        )
