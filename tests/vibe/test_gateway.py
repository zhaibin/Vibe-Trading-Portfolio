import json

import httpx
import pytest
import respx

from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.errors import GatewayError, GatewayErrorCode
from vibe_portfolio.vibe.gateway import VibeGateway
from vibe_portfolio.vibe.sse import SseEvent


@pytest.fixture
def gateway() -> VibeGateway:
    return VibeGateway(Settings(vibe_api_key="test-key"))


@respx.mock
async def test_reads_api_info_and_openapi(gateway: VibeGateway) -> None:
    api_route = respx.get("http://127.0.0.1:8899/api").mock(
        return_value=httpx.Response(
            200,
            json={"service": "Vibe-Trading API", "version": "0.1.11", "docs": "/docs", "health": "/health"},
        )
    )
    spec_route = respx.get("http://127.0.0.1:8899/openapi.json").mock(
        return_value=httpx.Response(200, json={"openapi": "3.1.0", "paths": {"/sessions": {"post": {}}}})
    )

    assert (await gateway.api_info()).version == "0.1.11"
    assert "/sessions" in (await gateway.openapi())["paths"]
    assert api_route.calls[0].request.headers["Authorization"] == "Bearer test-key"
    assert spec_route.called
    await gateway.close()


@respx.mock
async def test_health_and_readiness_require_public_dto_statuses(gateway: VibeGateway) -> None:
    health_payload = {
        "status": "healthy",
        "service": "Vibe-Trading API",
        "timestamp": "2026-07-18T00:00:00Z",
    }
    ready_payload = {
        "status": "ready",
        "service": "Vibe-Trading API",
        "timestamp": "2026-07-18T00:00:00Z",
    }
    respx.get("http://127.0.0.1:8899/live").mock(return_value=httpx.Response(200, json=health_payload))
    respx.get("http://127.0.0.1:8899/health").mock(return_value=httpx.Response(200, json=health_payload))
    respx.get("http://127.0.0.1:8899/ready").mock(return_value=httpx.Response(200, json=ready_payload))

    assert (await gateway.live()).ok is True
    assert (await gateway.health()).status == "healthy"
    assert (await gateway.ready()).ok is True
    await gateway.close()


@pytest.mark.parametrize("path", ["/live", "/health", "/ready"])
@respx.mock
async def test_health_dto_drift_maps_to_contract_error(gateway: VibeGateway, path: str) -> None:
    respx.get(f"http://127.0.0.1:8899{path}").mock(
        return_value=httpx.Response(200, json={"status": "unexpected"})
    )

    with pytest.raises(GatewayError) as error:
        if path == "/live":
            await gateway.live()
        elif path == "/health":
            await gateway.health()
        else:
            await gateway.ready()

    assert error.value.code is GatewayErrorCode.VIBE_CONTRACT_ERROR
    await gateway.close()


@respx.mock
async def test_research_calls_use_public_contract_and_fixed_risk_tier(gateway: VibeGateway) -> None:
    session_route = respx.post("http://127.0.0.1:8899/sessions").mock(
        return_value=httpx.Response(
            201,
            json={
                "session_id": "session-1",
                "title": "Portfolio compatibility probe",
                "status": "active",
                "created_at": "2026-07-18T00:00:00Z",
                "updated_at": "2026-07-18T00:00:00Z",
                "last_attempt_id": None,
            },
        )
    )
    goal_route = respx.post("http://127.0.0.1:8899/sessions/session-1/goal").mock(
        return_value=httpx.Response(
            201,
            json={"goal": {"goal_id": "goal-1"}, "claims": [], "criteria": [], "evidence": [], "evidence_count": 0},
        )
    )
    message_route = respx.post("http://127.0.0.1:8899/sessions/session-1/messages").mock(
        return_value=httpx.Response(200, json={"message_id": "message-1", "attempt_id": "attempt-1"})
    )

    session = await gateway.create_session("Portfolio compatibility probe")
    await gateway.create_research_goal(
        session.session_id,
        "Verify read-only Portfolio context",
        ["Call the approved Portfolio MCP tool"],
    )
    accepted = await gateway.send_message(session.session_id, "Read-only compatibility check")

    assert accepted.attempt_id == "attempt-1"
    assert session_route.calls[0].request.content == b'{"title":"Portfolio compatibility probe","config":{}}'
    assert b'"risk_tier":"research_general"' in goal_route.calls[0].request.content
    assert b"mcpServers" not in session_route.calls[0].request.content
    message_payload = json.loads(message_route.calls[0].request.content)
    assert message_payload["content"].startswith("Read-only compatibility check")
    assert "Do not place orders" in message_payload["content"]
    assert "broker writes" in message_payload["content"]
    assert "execute trades" in message_payload["content"]
    assert len(message_payload["content"]) <= gateway.settings.vibe_message_limit
    assert message_route.called
    await gateway.close()


@respx.mock
async def test_maps_auth_and_offline_failures(gateway: VibeGateway) -> None:
    route = respx.get("http://127.0.0.1:8899/api").mock(return_value=httpx.Response(401, json={"detail": "invalid"}))

    with pytest.raises(GatewayError) as auth_error:
        await gateway.api_info()
    assert auth_error.value.code is GatewayErrorCode.VIBE_AUTH_FAILED

    route.mock(side_effect=httpx.ConnectError("offline"))
    with pytest.raises(GatewayError) as offline_error:
        await gateway.api_info()
    assert offline_error.value.code is GatewayErrorCode.VIBE_UNAVAILABLE
    await gateway.close()


async def test_rejects_messages_larger_than_bounded_context(gateway: VibeGateway) -> None:
    with pytest.raises(ValueError, match="4,000"):
        await gateway.send_message("session-1", "x" * 4001)
    await gateway.close()


async def test_rejects_message_when_safety_instructions_exceed_configured_limit() -> None:
    gateway = VibeGateway(Settings(vibe_api_key="test-key", vibe_message_limit=100))

    with pytest.raises(ValueError, match="100"):
        await gateway.send_message("session-1", "x" * 100)
    await gateway.close()


@respx.mock
async def test_maps_malformed_json_to_contract_error(gateway: VibeGateway) -> None:
    respx.get("http://127.0.0.1:8899/api").mock(return_value=httpx.Response(200, content=b"not json"))

    with pytest.raises(GatewayError) as error:
        await gateway.api_info()
    assert error.value.code is GatewayErrorCode.VIBE_CONTRACT_ERROR
    await gateway.close()


@respx.mock
async def test_maps_invalid_dto_payload_to_contract_error(gateway: VibeGateway) -> None:
    respx.get("http://127.0.0.1:8899/api").mock(return_value=httpx.Response(200, json={"service": "Vibe-Trading API"}))

    with pytest.raises(GatewayError) as error:
        await gateway.api_info()
    assert error.value.code is GatewayErrorCode.VIBE_CONTRACT_ERROR
    await gateway.close()


@respx.mock
async def test_maps_non_list_messages_to_contract_error(gateway: VibeGateway) -> None:
    respx.get(url__startswith="http://127.0.0.1:8899/sessions/session-1/messages").mock(
        return_value=httpx.Response(200, json={"message_id": "message-1"})
    )

    with pytest.raises(GatewayError) as error:
        await gateway.list_messages("session-1")
    assert error.value.code is GatewayErrorCode.VIBE_CONTRACT_ERROR
    await gateway.close()


@respx.mock
async def test_supports_poll_ticket_and_cancel_contracts(gateway: VibeGateway) -> None:
    respx.get(url__startswith="http://127.0.0.1:8899/sessions/session-1/messages").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "message_id": "message-2",
                    "session_id": "session-1",
                    "role": "assistant",
                    "content": "done",
                    "created_at": "2026-07-18T00:00:01Z",
                    "linked_attempt_id": "attempt-1",
                    "metadata": {"status": "completed"},
                }
            ],
        )
    )
    respx.post("http://127.0.0.1:8899/auth/sse-ticket").mock(
        return_value=httpx.Response(200, json={"ticket": "ticket-1"})
    )
    respx.post("http://127.0.0.1:8899/sessions/session-1/cancel").mock(
        return_value=httpx.Response(200, json={"status": "cancelled"})
    )

    assert (await gateway.list_messages("session-1"))[0].linked_attempt_id == "attempt-1"
    assert (await gateway.mint_sse_ticket()).ticket == "ticket-1"
    assert (await gateway.cancel("session-1")).status == "cancelled"
    await gateway.close()


@respx.mock
async def test_stream_events_uses_ticket_replay_and_last_event_id(gateway: VibeGateway) -> None:
    respx.post("http://127.0.0.1:8899/auth/sse-ticket").mock(
        return_value=httpx.Response(200, json={"ticket": "ticket-2"})
    )
    event_route = respx.get(url__startswith="http://127.0.0.1:8899/sessions/session-1/events").mock(
        return_value=httpx.Response(
            200,
            text='id: e2\nevent: attempt.completed\ndata: {"attempt_id":"attempt-1"}\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )

    events = [event async for event in gateway.stream_events("session-1", "e1")]

    assert events == [SseEvent("e2", "attempt.completed", {"attempt_id": "attempt-1"})]
    request = event_route.calls[0].request
    assert request.url.params["ticket"] == "ticket-2"
    assert request.url.params["replay"] == "active"
    assert request.headers["Last-Event-ID"] == "e1"
    await gateway.close()


@respx.mock
async def test_each_stream_opening_mints_a_fresh_one_shot_ticket(gateway: VibeGateway) -> None:
    ticket_route = respx.post("http://127.0.0.1:8899/auth/sse-ticket").mock(
        side_effect=[
            httpx.Response(200, json={"ticket": "ticket-1"}),
            httpx.Response(200, json={"ticket": "ticket-2"}),
        ]
    )
    event_route = respx.get(url__startswith="http://127.0.0.1:8899/sessions/session-1/events").mock(
        return_value=httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
    )

    assert [event async for event in gateway.stream_events("session-1")] == []
    assert [event async for event in gateway.stream_events("session-1", "e1")] == []

    assert ticket_route.call_count == 2
    assert [call.request.url.params["ticket"] for call in event_route.calls] == ["ticket-1", "ticket-2"]
    assert event_route.calls[0].request.url.path == event_route.calls[1].request.url.path
    assert event_route.calls[1].request.headers["Last-Event-ID"] == "e1"
    await gateway.close()
