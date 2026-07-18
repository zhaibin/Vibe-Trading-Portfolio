from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from mcp.types import ToolAnnotations

from vibe_portfolio.config import Settings

MCP_TOOL_NAME = "portfolio_get_capabilities"


def portfolio_get_capabilities() -> dict[str, object]:
    """Return the read-only Portfolio MCP contract supported by this spike."""
    return {
        "schema_version": "portfolio-mcp.v1",
        "mode": "compatibility_spike",
        "read_only": True,
        "tools": [MCP_TOOL_NAME],
        "mutations": [],
    }


def build_mcp_server(token: str) -> FastMCP:
    """Build the closed-world MCP server protected by a dedicated token."""
    if not token.strip():
        raise ValueError("Portfolio MCP token must not be empty")
    verifier = StaticTokenVerifier(
        tokens={token: {"client_id": "vibe-trading", "scopes": ["portfolio.read"]}},
        required_scopes=["portfolio.read"],
    )
    server = FastMCP(name="Vibe-Trading Portfolio", version="0.1.0", auth=verifier)
    server.tool(
        name=MCP_TOOL_NAME,
        description="Read the Portfolio MCP compatibility contract. This tool never mutates portfolio data.",
        annotations=ToolAnnotations(
            title="Portfolio capabilities",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )(portfolio_get_capabilities)
    return server


def _read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"Portfolio MCP token file is empty: {path}")
    return token


def main() -> None:
    """Run the authenticated MCP server on its configured loopback address."""
    settings = Settings()
    server = build_mcp_server(_read_token(settings.mcp_token_file))
    server.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path="/mcp",
    )


if __name__ == "__main__":
    main()
