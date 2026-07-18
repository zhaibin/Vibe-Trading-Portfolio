from dataclasses import dataclass
from typing import Protocol

from vibe_portfolio.compatibility import McpStatus
from vibe_portfolio.vibe.models import CancelResult
from vibe_portfolio.vibe.research import ResearchCoordinator, ResearchGateway
from vibe_portfolio.vibe.watcher import AttemptOutcome, AttemptStatus

EXPECTED_VIBE_TOOL_NAME = "mcp_portfolio_portfolio_get_capabilities"

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
        started = await ResearchCoordinator(self.gateway).start(
            title="Portfolio MCP compatibility probe",
            objective="Verify the operator-approved read-only Portfolio MCP boundary",
            criteria=[
                "Observe the exact Portfolio MCP tool call",
                "Observe a successful tool result",
                "Perform no order placement or portfolio mutation",
            ],
            message=PROBE_MESSAGE,
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

        tool_calls = [
            (index, event)
            for index, event in enumerate(outcome.events)
            if event.event_type == "tool_call"
            and event.data.get("attempt_id") == started.attempt_id
            and event.data.get("tool")
        ]
        observed_tools = [str(event.data["tool"]) for _, event in tool_calls]

        if outcome.status is AttemptStatus.TIMED_OUT:
            await self.gateway.cancel(started.session_id)
            return McpProbeResult(
                McpStatus.FAILED,
                started.session_id,
                started.attempt_id,
                observed_tools,
                "probe_timed_out",
            )
        if outcome.status is not AttemptStatus.COMPLETED:
            return McpProbeResult(
                McpStatus.FAILED,
                started.session_id,
                started.attempt_id,
                observed_tools,
                f"probe_attempt_{outcome.status.value}",
            )
        if not observed_tools:
            return McpProbeResult(
                McpStatus.MISSING,
                started.session_id,
                started.attempt_id,
                observed_tools,
                "expected_tool_call_not_observed",
            )
        if observed_tools != [EXPECTED_VIBE_TOOL_NAME]:
            return McpProbeResult(
                McpStatus.FAILED,
                started.session_id,
                started.attempt_id,
                observed_tools,
                "unexpected_tool_calls_observed",
            )

        tool_results = [
            (index, event)
            for index, event in enumerate(outcome.events)
            if event.event_type == "tool_result"
            and event.data.get("attempt_id") == started.attempt_id
        ]
        if not tool_results:
            return McpProbeResult(
                McpStatus.FAILED,
                started.session_id,
                started.attempt_id,
                observed_tools,
                "tool_result_not_successful",
            )
        if (
            len(tool_results) != 1
            or tool_results[0][1].data.get("tool") != EXPECTED_VIBE_TOOL_NAME
        ):
            return McpProbeResult(
                McpStatus.FAILED,
                started.session_id,
                started.attempt_id,
                observed_tools,
                "unexpected_tool_results_observed",
            )
        result_index, result_event = tool_results[0]
        if result_index <= tool_calls[0][0]:
            return McpProbeResult(
                McpStatus.FAILED,
                started.session_id,
                started.attempt_id,
                observed_tools,
                "tool_result_not_after_call",
            )
        if result_event.data.get("status") != "ok":
            return McpProbeResult(
                McpStatus.FAILED,
                started.session_id,
                started.attempt_id,
                observed_tools,
                "tool_result_not_successful",
            )
        return McpProbeResult(
            McpStatus.AVAILABLE,
            started.session_id,
            started.attempt_id,
            observed_tools,
        )
