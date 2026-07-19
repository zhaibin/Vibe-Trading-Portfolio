"""Loopback same-origin request and browser response hardening."""

from collections.abc import Sequence

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vibe_portfolio.config import Settings

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)
PERMISSIONS_POLICY = "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_JSON_METHODS = frozenset({"POST", "PATCH"})
_PROBE_PATH = "/api/v1/system/compatibility/mcp-probe"


def _error(code: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code}}, status_code=status_code)


def _media_type(headers: Headers) -> str:
    return headers.get("content-type", "").partition(";")[0].strip().lower()


class SecurityMiddleware:
    """Enforce the approved no-auth loopback profile at the ASGI boundary."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.max_request_bytes = settings.api_max_request_bytes
        self.allowed_origins = settings.api_origins()
        self.allowed_hosts = frozenset(
            {f"127.0.0.1:{settings.api_port}", f"localhost:{settings.api_port}"}
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if headers.get("host") not in self.allowed_hosts:
            await self._respond(_error("HOST_FORBIDDEN", 400), scope, receive, send)
            return
        if method in _WRITE_METHODS:
            if headers.get("origin") not in self.allowed_origins:
                await self._respond(_error("ORIGIN_FORBIDDEN", 403), scope, receive, send)
                return
            if headers.get("sec-fetch-site") not in {None, "same-origin"}:
                await self._respond(_error("CROSS_SITE_FORBIDDEN", 403), scope, receive, send)
                return
            if method in _JSON_METHODS and path.startswith("/api/v1/") and path != _PROBE_PATH:
                if _media_type(headers) != "application/json":
                    await self._respond(_error("JSON_REQUIRED", 415), scope, receive, send)
                    return
            messages = await self._bounded_body(receive)
            if messages is None:
                await self._respond(_error("REQUEST_TOO_LARGE", 413), scope, receive, send)
                return
            receive = self._replay(messages)

        await self.app(scope, receive, self._send_with_headers(scope, send))

    async def _bounded_body(self, receive: Receive) -> list[Message] | None:
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                return messages
            size += len(message.get("body", b""))
            if size > self.max_request_bytes:
                return None
            if not message.get("more_body", False):
                return messages

    @staticmethod
    def _replay(messages: Sequence[Message]) -> Receive:
        pending = list(messages)

        async def receive() -> Message:
            if pending:
                return pending.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        return receive

    def _send_with_headers(self, scope: Scope, send: Send) -> Send:
        async def hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = PERMISSIONS_POLICY
                path = scope.get("path", "")
                if path == "/api" or path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        return hardened

    async def _respond(
        self, response: JSONResponse, scope: Scope, receive: Receive, send: Send
    ) -> None:
        await response(scope, receive, self._send_with_headers(scope, send))
