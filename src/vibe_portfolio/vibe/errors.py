from enum import StrEnum


class GatewayErrorCode(StrEnum):
    VIBE_UNAVAILABLE = "VIBE_UNAVAILABLE"
    VIBE_TIMEOUT = "VIBE_TIMEOUT"
    VIBE_AUTH_FAILED = "VIBE_AUTH_FAILED"
    VIBE_CONTRACT_ERROR = "VIBE_CONTRACT_ERROR"
    VIBE_UPSTREAM_ERROR = "VIBE_UPSTREAM_ERROR"


class GatewayError(RuntimeError):
    """Stable Sidecar-facing error that does not leak upstream response bodies."""

    def __init__(self, code: GatewayErrorCode, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
