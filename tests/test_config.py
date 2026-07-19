from pathlib import Path

import pytest
from pydantic import ValidationError

from vibe_portfolio.config import Settings


def test_market_refresh_lease_must_safely_exceed_whole_operation_deadline() -> None:
    with pytest.raises(ValidationError):
        Settings(market_operation_timeout_seconds=15, market_refresh_lease_seconds=20)
    settings = Settings(market_operation_timeout_seconds=15, market_refresh_lease_seconds=30)
    assert settings.market_refresh_lease_seconds == 30


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


def test_portfolio_runtime_defaults_are_local_and_bounded() -> None:
    settings = Settings(_env_file=None)

    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8765
    assert settings.api_origin() == "http://127.0.0.1:8765"
    assert settings.api_origins() == frozenset({"http://127.0.0.1:8765", "http://localhost:8765"})
    assert settings.database_path == Path("var/data/portfolio.db")
    assert settings.api_max_request_bytes == 64_000
    assert settings.database_busy_timeout_ms == 500
    assert settings.market_connect_timeout_seconds == 3.0
    assert settings.market_read_timeout_seconds == 8.0
    assert settings.market_operation_timeout_seconds == 15.0
    assert settings.market_refresh_lease_seconds == 90.0
    assert settings.market_max_concurrency == 4
    assert settings.market_max_batch_instruments == 500
    assert settings.market_max_response_bytes == 1_000_000


def test_api_host_cannot_be_widened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTFOLIO_API_HOST", "0.0.0.0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
