from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ApiInfo(ContractModel):
    service: str
    version: str
    docs: str
    health: str


class ProbeResult(BaseModel):
    ok: bool
    status_code: int
    detail: str | None = None


class SessionRecord(ContractModel):
    session_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    last_attempt_id: str | None = None


class GoalSnapshot(ContractModel):
    goal: dict[str, Any]
    claims: list[dict[str, Any]] = Field(default_factory=list)
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    evidence_count: int = 0


class MessageAccepted(ContractModel):
    message_id: str
    attempt_id: str


class MessageRecord(ContractModel):
    message_id: str
    session_id: str
    role: str
    content: str
    created_at: str
    linked_attempt_id: str | None = None
    metadata: dict[str, Any] | None = None


class CancelResult(ContractModel):
    status: str


class SseTicket(ContractModel):
    ticket: str
