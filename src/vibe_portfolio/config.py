from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Sidecar settings loaded from PORTFOLIO_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vibe_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8899")
    vibe_api_key: SecretStr | None = None
    vibe_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    vibe_read_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    vibe_analysis_timeout_seconds: float = Field(default=300.0, gt=0, le=1800)
    vibe_poll_interval_seconds: float = Field(default=1.0, gt=0, le=10)
    vibe_message_limit: int = Field(default=4000, ge=1, le=4000)

    mcp_host: Literal["127.0.0.1"] = "127.0.0.1"
    mcp_port: int = Field(default=8766, ge=1024, le=65535)
    mcp_token_file: Path = Path("var/secrets/mcp-token")

    def vibe_base_url_text(self) -> str:
        """Return a normalized base URL without a trailing slash."""
        return str(self.vibe_base_url).rstrip("/")

    def vibe_auth_headers(self) -> dict[str, str]:
        """Return a Bearer header only when a Vibe API key is configured."""
        if self.vibe_api_key is None:
            return {}
        token = self.vibe_api_key.get_secret_value().strip()
        return {"Authorization": f"Bearer {token}"} if token else {}
