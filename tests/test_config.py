from pathlib import Path

import pytest
from pydantic import ValidationError

from vibe_portfolio.config import Settings


def test_settings_use_loopback_defaults_and_hide_secrets() -> None:
    settings = Settings(vibe_api_key="vibe-secret")

    assert settings.vibe_base_url_text() == "http://127.0.0.1:8899"
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8766
    assert settings.vibe_auth_headers() == {"Authorization": "Bearer vibe-secret"}
    assert "vibe-secret" not in repr(settings)


def test_mcp_listener_cannot_be_configured_non_loopback() -> None:
    with pytest.raises(ValidationError):
        Settings(mcp_host="0.0.0.0")


def test_message_limit_and_token_path_are_explicit() -> None:
    settings = Settings(mcp_token_file=Path("var/test-token"))

    assert settings.vibe_message_limit == 4000
    assert settings.mcp_token_file == Path("var/test-token")
