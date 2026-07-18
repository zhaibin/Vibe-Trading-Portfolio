import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

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
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.gateway = gateway
        self.discovery = discovery
        self.stream_warmup_seconds = stream_warmup_seconds
        self.event_timeout_seconds = event_timeout_seconds
        self.sleep = sleep

    async def verify(self) -> RuntimeContractResult:
        stage = "route_only"
        version: str | None = None
        session_id: str | None = None
        attempt_id: str | None = None
        stream_task: asyncio.Task[SseEvent] | None = None
        cancelled = False
        try:
            report = await self.discovery.discover(McpStatus.NOT_CHECKED)
            version = report.vibe_version
            if not report.contract_compatible:
                return self._failed(stage, "route_contract_incompatible", version=version)

            stage = "health"
            health = await self.gateway.health()
            if health.status != "healthy":
                return self._failed(stage, "health_not_healthy", version=version)

            stage = "ready"
            readiness = await self.gateway.ready()
            if not readiness.ok:
                return self._failed(stage, "vibe_not_ready", version=version)

            stage = "session"
            session = await self.gateway.create_session("Portfolio public runtime contract probe")
            session_id = session.session_id

            stage = "goal"
            goal = await self.gateway.create_research_goal(
                session_id,
                "Verify the public research-only Vibe runtime contract",
                ["Exercise only public runtime endpoints", "Perform no trading or broker writes"],
            )
            goal_id = goal.goal.get("goal_id")
            if not isinstance(goal_id, str) or not goal_id.strip():
                return self._failed(stage, "goal_dto_incompatible", version=version, session_id=session_id)

            stage = "ticket"
            ticket = await self.gateway.mint_sse_ticket()
            if not ticket.ticket.strip():
                return self._failed(stage, "ticket_dto_incompatible", version=version, session_id=session_id)

            stage = "sse"
            stream_task = asyncio.create_task(self._next_event(session_id, None))
            await self.sleep(self.stream_warmup_seconds)

            stage = "message"
            accepted = await self.gateway.send_message(
                session_id,
                "Runtime contract probe only. Return no investment advice and perform no external action.",
            )
            attempt_id = accepted.attempt_id

            stage = "sse"
            first_event = await asyncio.wait_for(stream_task, timeout=self.event_timeout_seconds)
            stream_task = None
            if not first_event.event_id:
                return self._failed(
                    stage,
                    "sse_event_id_missing",
                    version=version,
                    session_id=session_id,
                    attempt_id=attempt_id,
                )

            stage = "sse_replay"
            replay_event = await asyncio.wait_for(
                self._next_event(session_id, first_event.event_id),
                timeout=self.event_timeout_seconds,
            )
            if not replay_event.event_id or replay_event.event_id == first_event.event_id:
                return self._failed(
                    stage,
                    "sse_replay_incompatible",
                    version=version,
                    session_id=session_id,
                    attempt_id=attempt_id,
                )

            stage = "poll"
            messages = await self.gateway.list_messages(session_id, limit=100)
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
            cancel_result = await self.gateway.cancel(session_id)
            cancelled = True
            if cancel_result.status not in {"cancelled", "no_active_loop"}:
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
                first_event_id=first_event.event_id,
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
            if session_id is not None and not cancelled and stage != "cancel":
                try:
                    await self.gateway.cancel(session_id)
                except Exception:
                    pass

    async def _next_event(self, session_id: str, last_event_id: str | None) -> SseEvent:
        async for event in self.gateway.stream_events(session_id, last_event_id):
            return event
        raise GatewayError(GatewayErrorCode.VIBE_CONTRACT_ERROR, "Vibe SSE stream closed without an event")

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
