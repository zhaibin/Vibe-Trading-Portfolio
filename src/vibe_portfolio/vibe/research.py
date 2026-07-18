from dataclasses import dataclass
from typing import Protocol

from vibe_portfolio.vibe.models import GoalSnapshot, MessageAccepted, SessionRecord


class ResearchGateway(Protocol):
    async def create_session(self, title: str) -> SessionRecord:
        ...

    async def create_research_goal(
        self, session_id: str, objective: str, criteria: list[str]
    ) -> GoalSnapshot:
        ...

    async def send_message(self, session_id: str, content: str) -> MessageAccepted:
        ...


@dataclass(frozen=True, slots=True)
class StartedResearch:
    session_id: str
    goal_id: str
    message_id: str
    attempt_id: str


class ResearchCoordinator:
    """Create a Vibe research attempt using only its public Session contract."""

    def __init__(self, gateway: ResearchGateway) -> None:
        self.gateway = gateway

    async def start(
        self,
        *,
        title: str,
        objective: str,
        criteria: list[str],
        message: str,
    ) -> StartedResearch:
        session = await self.gateway.create_session(title)
        goal = await self.gateway.create_research_goal(
            session.session_id, objective, criteria
        )
        raw_goal_id = goal.goal.get("goal_id")
        if not isinstance(raw_goal_id, str) or not raw_goal_id.strip():
            raise ValueError("Vibe research goal response did not contain goal_id")
        goal_id = raw_goal_id
        accepted = await self.gateway.send_message(session.session_id, message)
        return StartedResearch(
            session.session_id,
            goal_id,
            accepted.message_id,
            accepted.attempt_id,
        )
