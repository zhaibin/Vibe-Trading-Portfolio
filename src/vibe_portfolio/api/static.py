"""Safe GET-only serving for packaged SPA files and client routes."""

import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from starlette.responses import FileResponse, JSONResponse, Response
from starlette.types import Receive, Scope, Send

_HASHED_ASSET = re.compile(r"-[A-Za-z0-9_-]{8,}(?=\.)")


def _not_found() -> JSONResponse:
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _unsafe_path(scope: Scope) -> bool:
    raw = scope.get("raw_path", b"")
    try:
        decoded = unquote(raw.decode("ascii")) if isinstance(raw, bytes) else unquote(str(raw))
    except UnicodeError:
        return True
    path = unquote(scope.get("path", ""))
    return (
        "\\" in decoded
        or "\\" in path
        or "\x00" in decoded
        or "\x00" in path
        or ".." in PurePosixPath(decoded).parts
        or ".." in PurePosixPath(path).parts
    )


class SpaStaticApp:
    """Serve immutable assets and safe extensionless SPA routes from one root."""

    def __init__(self, static_dir: Path) -> None:
        self.root = static_dir.resolve()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await _not_found()(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if _unsafe_path(scope) or path == "/api" or path.startswith("/api/"):
            await _not_found()(scope, receive, send)
            return
        if path == "/assets":
            await _not_found()(scope, receive, send)
            return
        if path.startswith("/assets/"):
            response = self._asset(path) if method in {"GET", "HEAD"} else _not_found()
            await response(scope, receive, send)
            return
        if method != "GET":
            response = JSONResponse(
                {"detail": "Method Not Allowed"}, status_code=405, headers={"Allow": "GET"}
            )
            await response(scope, receive, send)
            return
        if self._asset_like(path):
            await _not_found()(scope, receive, send)
            return
        index = self.root / "index.html"
        response = (
            FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-store"})
            if self._safe_file(index)
            else _not_found()
        )
        await response(scope, receive, send)

    def _asset(self, path: str) -> Response:
        relative = path.removeprefix("/")
        candidate = self.root / relative
        if not self._safe_file(candidate):
            return _not_found()
        cache = (
            "public, max-age=31536000, immutable"
            if _HASHED_ASSET.search(candidate.name)
            else "no-store"
        )
        return FileResponse(candidate, headers={"Cache-Control": cache})

    def _safe_file(self, candidate: Path) -> bool:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            return False
        return resolved.is_file()

    @staticmethod
    def _asset_like(path: str) -> bool:
        return any("." in part for part in PurePosixPath(path).parts)
