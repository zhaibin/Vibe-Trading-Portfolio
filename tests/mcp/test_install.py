import json
import stat
from pathlib import Path

import pytest

from vibe_portfolio.mcp.install import create_install_bundle


def test_bundle_is_owner_only_explicit_and_never_edits_vibe_config(tmp_path: Path) -> None:
    vibe_config = tmp_path / "agent.json"
    vibe_config.write_text('{"existing": true}\n', encoding="utf-8")
    output_dir = tmp_path / "bundle"

    bundle = create_install_bundle(output_dir, "http://127.0.0.1:8766/mcp", token="dedicated-token")
    snippet = json.loads(bundle.snippet_file.read_text(encoding="utf-8"))

    assert vibe_config.read_text(encoding="utf-8") == '{"existing": true}\n'
    assert snippet == {
        "mcpServers": {
            "portfolio": {
                "type": "streamableHttp",
                "url": "http://127.0.0.1:8766/mcp",
                "headers": {"Authorization": "Bearer dedicated-token"},
                "toolTimeout": 30.0,
                "initTimeout": 30.0,
                "enabledTools": ["portfolio_get_capabilities"],
            }
        }
    }
    assert "ALLOW_SESSION_MCP_SERVERS" not in bundle.snippet_file.read_text(encoding="utf-8")
    assert stat.S_IMODE(bundle.token_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(bundle.snippet_file.stat().st_mode) == 0o600


def test_bundle_refuses_to_overwrite_existing_secrets(tmp_path: Path) -> None:
    output_dir = tmp_path / "bundle"
    create_install_bundle(output_dir, "http://127.0.0.1:8766/mcp", token="first")

    with pytest.raises(FileExistsError):
        create_install_bundle(output_dir, "http://127.0.0.1:8766/mcp", token="second")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8766/mcp",
        "http://localhost:8766/mcp",
        "http://127.0.0.1:8766@evil.example/mcp",
    ],
)
def test_bundle_rejects_non_loopback_http_urls(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        create_install_bundle(tmp_path / "bundle", url, token="dedicated-token")


@pytest.mark.parametrize("token", ["", "   ", "\n"])
def test_bundle_rejects_empty_explicit_tokens(tmp_path: Path, token: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        create_install_bundle(
            tmp_path / "bundle",
            "http://127.0.0.1:8766/mcp",
            token=token,
        )
