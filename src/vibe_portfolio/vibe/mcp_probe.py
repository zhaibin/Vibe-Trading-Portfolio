import asyncio
from dataclasses import dataclass
from typing import Protocol

from vibe_portfolio.compatibility import McpStatus
from vibe_portfolio.vibe.models import CancelResult
from vibe_portfolio.vibe.research import ResearchCoordinator, ResearchGateway
from vibe_portfolio.vibe.watcher import AttemptOutcome, AttemptStatus

EXPECTED_VIBE_TOOL_NAME = "mcp_portfolio_portfolio_get_capabilities"
ALLOWED_GOAL_CONTROL_TOOLS = frozenset(
    {"get_research_goal", "add_goal_evidence", "update_research_goal_status"}
)
ALLOWED_PROBE_TOOLS = ALLOWED_GOAL_CONTROL_TOOLS | {EXPECTED_VIBE_TOOL_NAME}

PROBE_MESSAGE = """Run a read-only compatibility check.
Call exactly mcp_portfolio_portfolio_get_capabilities once and summarize its schema_version and read_only fields.
Do not place orders, do not call broker-write tools, do not execute trades, and do not modify portfolio data.
This is a protocol test, not investment advice."""


class ProbeGateway(ResearchGateway, Protocol):
    async def cancel(self, session_id: str) -> CancelResult:
        ...


class ProbeWatcher(Protocol):
    async def wait(self, session_id: str, attempt_id: str) -> AttemptOutcome:
        ...


@dataclass(frozen=True, slots=True)
class McpProbeResult:
    status: McpStatus
    session_id: str
    attempt_id: str
    observed_tools: list[str]
    reason: str | None = None


class PortfolioMcpProbe:
    """Prove Vibe can invoke the approved MCP tool from observed attempt events."""

    def __init__(self, gateway: ProbeGateway, watcher: ProbeWatcher) -> None:
        self.gateway = gateway
        self.watcher = watcher

    async def run(self) -> McpProbeResult:
        session_id: str | None = None
        cleanup_required = False
        cancel_attempted = False

        def record_session(created_session_id: str) -> None:
            nonlocal session_id, cleanup_required
            session_id = created_session_id
            cleanup_required = True

        async def best_effort_cancel() -> None:
            nonlocal cancel_attempted
            if session_id is None or cancel_attempted:
                return
            cancel_attempted = True
            try:
                await asyncio.wait_for(self.gateway.cancel(session_id), timeout=5.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        try:
            started = await ResearchCoordinator(self.gateway).start(
                title="Portfolio MCP compatibility probe",
                objective="Verify the operator-approved read-only Portfolio MCP boundary",
                criteria=[
                    "Observe the exact Portfolio MCP tool call",
                    "Observe a successful tool result",
                    "Perform no order placement or portfolio mutation",
                ],
                message=PROBE_MESSAGE,
                on_session_created=record_session,
            )
            outcome = await self.watcher.wait(started.session_id, started.attempt_id)
            if outcome.session_id != started.session_id:
                return McpProbeResult(
                    McpStatus.FAILED,
                    started.session_id,
                    started.attempt_id,
                    [],
                    "probe_outcome_session_mismatch",
                )
            if outcome.attempt_id != started.attempt_id:
                return McpProbeResult(
                    McpStatus.FAILED,
                    started.session_id,
                    started.attempt_id,
                    [],
                    "probe_outcome_attempt_mismatch",
                )

            result = self._result_for_outcome(started.session_id, started.attempt_id, outcome)
            if result.status is McpStatus.AVAILABLE or self._outcome_proves_terminal(
                outcome, started.session_id, started.attempt_id
            ):
                cleanup_required = False
            return result
        finally:
            if cleanup_required:
                await best_effort_cancel()

    @staticmethod
    def _outcome_proves_terminal(
        outcome: AttemptOutcome,
        session_id: str,
        attempt_id: str,
    ) -> bool:
        expected_status = {
            "attempt.failed": AttemptStatus.FAILED,
            "attempt.cancelled": AttemptStatus.CANCELLED,
        }
        terminal_event = outcome.terminal_event
        if terminal_event is not None:
            return (
                terminal_event.data.get("attempt_id") == attempt_id
                and expected_status.get(terminal_event.event_type) is outcome.status
            )

        assistant_message = outcome.assistant_message
        if assistant_message is None:
            return False
        polled_status = {
            "failed": AttemptStatus.FAILED,
            "cancelled": AttemptStatus.CANCELLED,
        }.get(str((assistant_message.metadata or {}).get("status")))
        return (
            assistant_message.session_id == session_id
            and assistant_message.linked_attempt_id == attempt_id
            and assistant_message.role == "assistant"
            and polled_status is outcome.status
        )

    @staticmethod
    def _result_for_outcome(
        session_id: str,
        attempt_id: str,
        outcome: AttemptOutcome,
    ) -> McpProbeResult:
        observed_tools = [
            str(event.data["tool"])
            for event in outcome.events
            if event.event_type == "tool_call"
            and event.data.get("attempt_id") == attempt_id
            and event.data.get("tool")
        ]

        if outcome.status is AttemptStatus.TIMED_OUT:
            return McpProbeResult(
                McpStatus.FAILED,
                session_id,
                attempt_id,
                observed_tools,
                "probe_timed_out",
            )
        if outcome.status is not AttemptStatus.COMPLETED:
            return McpProbeResult(
                McpStatus.FAILED,
                session_id,
                attempt_id,
                observed_tools,
                f"probe_attempt_{outcome.status.value}",
            )

        target_call_count = sum(tool == EXPECTED_VIBE_TOOL_NAME for tool in observed_tools)
        if target_call_count > 1:
            return McpProbeResult(
                McpStatus.FAILED,
                session_id,
                attempt_id,
                observed_tools,
                "unexpected_tool_calls_observed",
            )

        target_result_count = sum(
            event.event_type == "tool_result"
            and event.data.get("attempt_id") == attempt_id
            and event.data.get("tool") == EXPECTED_VIBE_TOOL_NAME
            for event in outcome.events
        )
        if target_result_count > 1:
            return McpProbeResult(
                McpStatus.FAILED,
                session_id,
                attempt_id,
                observed_tools,
                "unexpected_tool_results_observed",
            )

        pending_calls: dict[str, int] = {}
        target_call_index: int | None = None
        target_result_index: int | None = None
        for index, event in enumerate(outcome.events):
            if event.data.get("attempt_id") != attempt_id:
                continue
            if event.event_type not in {"tool_call", "tool_result"}:
                continue
            tool_value = event.data.get("tool")
            if not isinstance(tool_value, str) or not tool_value:
                reason = (
                    "unexpected_tool_calls_observed"
                    if event.event_type == "tool_call"
                    else "unexpected_tool_results_observed"
                )
                return McpProbeResult(
                    McpStatus.FAILED, session_id, attempt_id, observed_tools, reason
                )
            if tool_value not in ALLOWED_PROBE_TOOLS:
                reason = (
                    "unexpected_tool_calls_observed"
                    if event.event_type == "tool_call"
                    else "unexpected_tool_results_observed"
                )
                return McpProbeResult(
                    McpStatus.FAILED, session_id, attempt_id, observed_tools, reason
                )

            if event.event_type == "tool_call":
                pending_calls[tool_value] = pending_calls.get(tool_value, 0) + 1
                if tool_value == EXPECTED_VIBE_TOOL_NAME:
                    target_call_index = index
                continue

            if pending_calls.get(tool_value, 0) == 0:
                reason = (
                    "tool_result_not_after_call"
                    if tool_value == EXPECTED_VIBE_TOOL_NAME
                    else "orphaned_tool_result"
                )
                return McpProbeResult(
                    McpStatus.FAILED, session_id, attempt_id, observed_tools, reason
                )
            if event.data.get("status") != "ok":
                reason = (
                    "tool_result_not_successful"
                    if tool_value == EXPECTED_VIBE_TOOL_NAME
                    else "goal_control_result_not_successful"
                )
                return McpProbeResult(
                    McpStatus.FAILED, session_id, attempt_id, observed_tools, reason
                )
            pending_calls[tool_value] -= 1
            if tool_value == EXPECTED_VIBE_TOOL_NAME:
                target_result_index = index

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
        if any(
            count > 0
            for tool, count in pending_calls.items()
            if tool in ALLOWED_GOAL_CONTROL_TOOLS
        ):
            return McpProbeResult(
                McpStatus.FAILED,
                session_id,
                attempt_id,
                observed_tools,
                "goal_control_result_not_successful",
            )
        return McpProbeResult(
            McpStatus.AVAILABLE,
            session_id,
            attempt_id,
            observed_tools,
        )
