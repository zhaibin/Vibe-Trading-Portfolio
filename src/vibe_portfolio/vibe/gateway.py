from typing import Any, cast

import httpx

from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.models import (
    ApiInfo,
    CancelResult,
    GoalSnapshot,
    MessageAccepted,
    MessageRecord,
    ProbeResult,
    SessionRecord,
    SseTicket,
)


class VibeGateway:
    """The only component allowed to know Vibe-Trading HTTP details."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.vibe_base_url_text(),
            headers=settings.vibe_auth_headers(),
            timeout=httpx.Timeout(
                connect=settings.vibe_connect_timeout_seconds,
                read=settings.vibe_read_timeout_seconds,
                write=settings.vibe_read_timeout_seconds,
                pool=settings.vibe_connect_timeout_seconds,
            ),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, expected: set[int] | None = None, **kwargs: Any
    ) -> httpx.Response:
        accepted = expected or {200}
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise GatewayError(GatewayErrorCode.VIBE_TIMEOUT, "Vibe-Trading request timed out") from exc
        except httpx.RequestError as exc:
            raise GatewayError(GatewayErrorCode.VIBE_UNAVAILABLE, "Vibe-Trading is unavailable") from exc
        if response.status_code in accepted:
            return response
        self._raise_response_error(response)
        raise AssertionError("unreachable")

    @staticmethod
    def _raise_response_error(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise GatewayError(
                GatewayErrorCode.VIBE_AUTH_FAILED, "Vibe-Trading authentication failed", response.status_code
            )
        if response.status_code in {404, 405, 422, 501}:
            raise GatewayError(
                GatewayErrorCode.VIBE_CONTRACT_ERROR,
                "Vibe-Trading public contract is incompatible",
                response.status_code,
            )
        raise GatewayError(
            GatewayErrorCode.VIBE_UPSTREAM_ERROR, "Vibe-Trading returned an upstream error", response.status_code
        )

    async def api_info(self) -> ApiInfo:
        response = await self._request("GET", "/api")
        return ApiInfo.model_validate(response.json())

    async def openapi(self) -> dict[str, Any]:
        response = await self._request("GET", "/openapi.json")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
            raise GatewayError(GatewayErrorCode.VIBE_CONTRACT_ERROR, "Vibe-Trading OpenAPI document has no paths")
        return cast(dict[str, Any], payload)

    async def live(self) -> ProbeResult:
        response = await self._request("GET", "/live")
        return ProbeResult(ok=True, status_code=response.status_code)

    async def ready(self) -> ProbeResult:
        response = await self._request("GET", "/ready", expected={200, 503})
        detail: str | None = None
        if response.status_code == 503:
            body = response.json()
            detail = str(body.get("detail", "not ready")) if isinstance(body, dict) else "not ready"
        return ProbeResult(ok=response.status_code == 200, status_code=response.status_code, detail=detail)

    async def create_session(self, title: str) -> SessionRecord:
        response = await self._request("POST", "/sessions", expected={201}, json={"title": title, "config": {}})
        return SessionRecord.model_validate(response.json())

    async def create_research_goal(self, session_id: str, objective: str, criteria: list[str]) -> GoalSnapshot:
        payload = {
            "objective": objective,
            "criteria": criteria,
            "ui_summary": objective[:500],
            "protocol": "thesis_review",
            "risk_tier": "research_general",
        }
        response = await self._request("POST", f"/sessions/{session_id}/goal", expected={201}, json=payload)
        return GoalSnapshot.model_validate(response.json())

    async def send_message(self, session_id: str, content: str) -> MessageAccepted:
        if len(content) > self.settings.vibe_message_limit:
            raise ValueError("Vibe Session messages must not exceed the bounded 4,000 character context")
        response = await self._request("POST", f"/sessions/{session_id}/messages", json={"content": content})
        return MessageAccepted.model_validate(response.json())

    async def list_messages(self, session_id: str, limit: int = 100) -> list[MessageRecord]:
        response = await self._request("GET", f"/sessions/{session_id}/messages", params={"limit": limit})
        return [MessageRecord.model_validate(item) for item in response.json()]

    async def cancel(self, session_id: str) -> CancelResult:
        response = await self._request("POST", f"/sessions/{session_id}/cancel")
        return CancelResult.model_validate(response.json())

    async def mint_sse_ticket(self) -> SseTicket:
        response = await self._request("POST", "/auth/sse-ticket")
        return SseTicket.model_validate(response.json())
