import argparse
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from vibe_portfolio.mcp.server import MCP_TOOL_NAME


@dataclass(frozen=True, slots=True)
class InstallBundle:
    token_file: Path
    snippet_file: Path


def _write_owner_only(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def _require_loopback_http_url(mcp_url: str) -> None:
    parsed = urlsplit(mcp_url)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Milestone 0 MCP URL must use loopback http://127.0.0.1:<port>") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Milestone 0 MCP URL must use loopback http://127.0.0.1:<port>")


def create_install_bundle(output_dir: Path, mcp_url: str, *, token: str | None = None) -> InstallBundle:
    """Create owner-only token and review-before-install configuration files."""
    _require_loopback_http_url(mcp_url)
    if token is not None and not token.strip():
        raise ValueError("Portfolio MCP token must not be empty")

    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    token_file = output_dir / "mcp-token"
    snippet_file = output_dir / "vibe-portfolio-mcp-snippet.json"
    if token_file.exists() or snippet_file.exists():
        raise FileExistsError(
            "Install bundle already exists; remove or archive it explicitly before rotating the token"
        )

    dedicated_token = token if token is not None else secrets.token_urlsafe(32)
    snippet = {
        "mcpServers": {
            "portfolio": {
                "type": "streamableHttp",
                "url": mcp_url,
                "headers": {"Authorization": f"Bearer {dedicated_token}"},
                "toolTimeout": 30.0,
                "initTimeout": 30.0,
                "enabledTools": [MCP_TOOL_NAME],
            }
        }
    }
    _write_owner_only(token_file, f"{dedicated_token}\n")
    _write_owner_only(snippet_file, json.dumps(snippet, ensure_ascii=False, indent=2) + "\n")
    return InstallBundle(token_file=token_file, snippet_file=snippet_file)


def main() -> None:
    """Generate files for an operator to inspect and install manually."""
    parser = argparse.ArgumentParser(
        description="Generate a manually reviewed Vibe Portfolio MCP configuration snippet"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("var/install"))
    parser.add_argument("--url", default="http://127.0.0.1:8766/mcp")
    args = parser.parse_args()
    bundle = create_install_bundle(args.output_dir, args.url)
    print(f"Token file: {bundle.token_file}")
    print(f"Review and manually merge: {bundle.snippet_file}")


if __name__ == "__main__":
    main()
