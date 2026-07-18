import httpx
import pytest
from fastmcp import Client

from vibe_portfolio.mcp.server import MCP_TOOL_NAME, build_mcp_server, portfolio_get_capabilities


async def test_server_exposes_one_read_only_closed_world_tool() -> None:
    server = build_mcp_server("test-token")

    async with Client(server) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools] == [MCP_TOOL_NAME]
    assert tools[0].annotations is not None
    assert tools[0].annotations.readOnlyHint is True
    assert tools[0].annotations.destructiveHint is False
    assert tools[0].annotations.idempotentHint is True
    assert tools[0].annotations.openWorldHint is False


def test_capability_payload_is_structured_and_explicit() -> None:
    payload = portfolio_get_capabilities()

    assert payload == {
        "schema_version": "portfolio-mcp.v1",
        "mode": "compatibility_spike",
        "read_only": True,
        "tools": ["portfolio_get_capabilities"],
        "mutations": [],
    }


@pytest.mark.parametrize("token", ["", "   ", "\n"])
def test_server_rejects_empty_tokens(token: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        build_mcp_server(token)


async def test_http_boundary_requires_the_dedicated_bearer_token() -> None:
    server = build_mcp_server("dedicated-token")
    app = server.http_app(path="/mcp")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.post("/mcp", json=request, headers=headers)
            wrong = await client.post(
                "/mcp",
                json=request,
                headers={**headers, "Authorization": "Bearer wrong-token"},
            )
            accepted = await client.post(
                "/mcp",
                json=request,
                headers={**headers, "Authorization": "Bearer dedicated-token"},
            )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
