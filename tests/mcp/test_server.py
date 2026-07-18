import asyncio
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport

from vibe_portfolio.mcp.server import (
    MCP_TOOL_NAME,
    _read_token,
    build_mcp_server,
    portfolio_get_capabilities,
)


@asynccontextmanager
async def running_loopback_mcp_server(token: str) -> AsyncIterator[str]:
    app = build_mcp_server(token).http_app(path="/mcp")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            lifespan="on",
            access_log=False,
            log_level="critical",
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError("Ephemeral MCP server exited before startup")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        try:
            async with asyncio.timeout(5):
                await server_task
        finally:
            listener.close()


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


async def test_real_loopback_socket_authenticates_initialize_lists_and_calls_tool() -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "socket-test", "version": "1.0"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}

    async with running_loopback_mcp_server("dedicated-token") as url:
        async with httpx.AsyncClient(timeout=2) as http_client:
            missing = await http_client.post(url, json=initialize, headers=headers)
            wrong = await http_client.post(
                url,
                json=initialize,
                headers={**headers, "Authorization": "Bearer wrong-token"},
            )

        transport = StreamableHttpTransport(url, auth=BearerAuth("dedicated-token"))
        async with asyncio.timeout(5):
            async with Client(transport, timeout=2, init_timeout=2) as client:
                tools = await client.list_tools()
                result = await client.call_tool(MCP_TOOL_NAME)

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert [tool.name for tool in tools] == [MCP_TOOL_NAME]
    assert result.is_error is False
    assert result.data == portfolio_get_capabilities()


def test_token_reader_accepts_only_an_owner_only_regular_file(tmp_path: Path) -> None:
    token_file = tmp_path / "mcp-token"
    token_file.write_text("dedicated-token\n", encoding="utf-8")
    token_file.chmod(0o600)

    assert _read_token(token_file) == "dedicated-token"


def test_token_reader_rejects_path_replaced_between_lstat_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_file = tmp_path / "mcp-token"
    token_file.write_text("original-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    replacement = tmp_path / "replacement-token"
    replacement.write_text("replacement-token\n", encoding="utf-8")
    replacement.chmod(0o600)
    real_open = os.open

    def replace_then_open(path: Path, flags: int) -> int:
        replacement.replace(token_file)
        return real_open(path, flags)

    monkeypatch.setattr(os, "open", replace_then_open)

    with pytest.raises(ValueError, match="stable regular file"):
        _read_token(token_file)


def test_token_reader_rejects_group_or_other_permission_bits(tmp_path: Path) -> None:
    token_file = tmp_path / "mcp-token"
    token_file.write_text("dedicated-token\n", encoding="utf-8")
    token_file.chmod(0o644)

    with pytest.raises(PermissionError, match="owner-only"):
        _read_token(token_file)


def test_token_reader_rejects_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "actual-token"
    target.write_text("dedicated-token\n", encoding="utf-8")
    target.chmod(0o600)
    token_file = tmp_path / "mcp-token"
    token_file.symlink_to(target)

    with pytest.raises(ValueError, match="regular file"):
        _read_token(token_file)


def test_token_reader_rejects_a_directory(tmp_path: Path) -> None:
    token_directory = tmp_path / "mcp-token"
    token_directory.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="regular file"):
        _read_token(token_directory)
