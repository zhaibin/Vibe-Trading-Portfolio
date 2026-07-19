"""Fixed-host, rate-spaced, size-bounded provider HTTP transport."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from vibe_portfolio.config import Settings
from vibe_portfolio.market_data.models import ProviderErrorCode, ProviderFailure

_USER_AGENT: Final = "Vibe-Trading-Portfolio/0.1 market-data"
_JSON_ACCEPT: Final = "application/json, application/*+json"
_TEXT_ACCEPT: Final = "text/plain"
_MAX_JSON_NESTING: Final = 64


@dataclass(frozen=True, slots=True)
class HostPolicy:
    path_prefix: str
    minimum_interval_seconds: float
    allow_suffix: bool = False


HOST_POLICIES = MappingProxyType(
    {
        "searchapi.eastmoney.com": HostPolicy("/api/suggest/get", 1.0),
        "push2.eastmoney.com": HostPolicy("/api/qt/stock/get", 1.0),
        "query1.finance.yahoo.com": HostPolicy("/v8/finance/chart/", 0.6, allow_suffix=True),
        "query2.finance.yahoo.com": HostPolicy("/v1/finance/search", 0.6),
        "qt.gtimg.cn": HostPolicy("/q=", 0.5, allow_suffix=True),
    }
)


@dataclass(slots=True)
class _HostState:
    lock: asyncio.Lock
    last_started: float | None = None


class BoundedProviderHttp:
    """Issue GET requests only to reviewed provider destinations."""

    def __init__(
        self,
        *,
        allowed_hosts: Collection[str],
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.MockTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        requested_hosts = frozenset(allowed_hosts)
        if not requested_hosts or not requested_hosts <= HOST_POLICIES.keys():
            raise ValueError("allowed_hosts must be a non-empty subset of code-defined hosts")
        self.settings = settings or Settings()
        self._allowed_hosts = requested_hosts
        self._monotonic = monotonic
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(self.settings.market_max_concurrency)
        self._host_states = {host: _HostState(asyncio.Lock()) for host in requested_hosts}
        if client is not None and transport is not None:
            raise ValueError("provide only one test transport seam")
        if client is not None:
            transport = getattr(client, "_transport", None)
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("only httpx.MockTransport may be injected")
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                connect=self.settings.market_connect_timeout_seconds,
                read=self.settings.market_read_timeout_seconds,
                write=self.settings.market_read_timeout_seconds,
                pool=self.settings.market_connect_timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=self.settings.market_max_concurrency,
                max_keepalive_connections=self.settings.market_max_concurrency,
            ),
            follow_redirects=False,
            trust_env=False,
            verify=True,
            headers={"User-Agent": _USER_AGENT},
            cookies={},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BoundedProviderHttp":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def get_json(self, url: str) -> object:
        host, request = self._build_request(url, accept=_JSON_ACCEPT)
        try:
            deadline = asyncio.get_running_loop().time() + self.settings.market_operation_timeout_seconds
            async with asyncio.timeout_at(deadline):
                payload, content_type = await self._send_bounded(request, host)
                return await asyncio.to_thread(_decode_json, payload, content_type)
        except ProviderFailure:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderFailure(ProviderErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
        except (
            UnicodeError,
            json.JSONDecodeError,
            InvalidOperation,
            MemoryError,
            OverflowError,
            RecursionError,
            ValueError,
        ):
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
        except Exception:
            # Fail closed at the untrusted transport/parser boundary without leaking details.
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None

    async def get_text(self, url: str, *, encoding: str = "gb18030") -> str:
        if encoding != "gb18030":
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
        host, request = self._build_request(url, accept=_TEXT_ACCEPT)
        try:
            deadline = asyncio.get_running_loop().time() + self.settings.market_operation_timeout_seconds
            async with asyncio.timeout_at(deadline):
                payload, _ = await self._send_bounded(request, host)
                return await asyncio.to_thread(payload.decode, "gb18030", "strict")
        except ProviderFailure:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderFailure(ProviderErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
        except (MemoryError, UnicodeError):
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
        except Exception:
            # Fail closed at the untrusted transport/decoder boundary without leaking details.
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None

    def _build_request(self, url: str, *, accept: str) -> tuple[str, httpx.Request]:
        try:
            destination = urlsplit(url)
            host = destination.hostname
            port = destination.port
        except ValueError:
            raise ProviderFailure(ProviderErrorCode.DESTINATION_BLOCKED) from None
        raw_path = destination.path
        if (
            destination.scheme != "https"
            or host is None
            or destination.netloc != host
            or destination.username is not None
            or destination.password is not None
            or port is not None
            or destination.fragment
            or host not in self._allowed_hosts
            or any(character.isspace() or ord(character) == 0x7F for character in url)
            or "%" in raw_path
            or "\\" in raw_path
            or not _path_characters_safe(raw_path)
            or any(segment in {".", ".."} for segment in raw_path.split("/"))
        ):
            raise ProviderFailure(ProviderErrorCode.DESTINATION_BLOCKED) from None
        try:
            request = httpx.Request("GET", url, headers={"User-Agent": _USER_AGENT, "Accept": accept})
        except Exception:
            raise ProviderFailure(ProviderErrorCode.DESTINATION_BLOCKED) from None
        normalized_path = request.url.path
        emitted_path = request.url.raw_path.partition(b"?")[0]
        if (
            request.url.scheme != "https"
            or request.url.host != host
            or request.url.port is not None
            or normalized_path != raw_path
            or emitted_path != raw_path.encode("ascii")
            or not _path_allowed(normalized_path, HOST_POLICIES[host])
        ):
            raise ProviderFailure(ProviderErrorCode.DESTINATION_BLOCKED) from None
        return host, request

    async def _send_bounded(self, request: httpx.Request, host: str) -> tuple[bytes, str | None]:
        policy = HOST_POLICIES[host]
        state = self._host_states[host]
        response: httpx.Response | None = None
        slot_acquired = False
        try:
            async with state.lock:
                if state.last_started is not None:
                    wait = policy.minimum_interval_seconds - (self._monotonic() - state.last_started)
                    if wait > 0:
                        await self._sleep(wait)
                await self._semaphore.acquire()
                slot_acquired = True
                state.last_started = self._monotonic()
                self._client.cookies.clear()
                try:
                    response = await self._client.send(
                        request,
                        stream=True,
                        follow_redirects=False,
                        auth=None,
                    )
                finally:
                    self._client.cookies.clear()
            if 300 <= response.status_code < 400:
                raise ProviderFailure(ProviderErrorCode.DESTINATION_BLOCKED) from None
            if not 200 <= response.status_code < 300:
                raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    parsed_length = int(declared_length)
                except ValueError:
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
                if parsed_length < 0:
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from None
                if parsed_length > self.settings.market_max_response_bytes:
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_TOO_LARGE) from None
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > self.settings.market_max_response_bytes:
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_TOO_LARGE) from None
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type")
        finally:
            try:
                if response is not None:
                    await response.aclose()
            finally:
                if slot_acquired:
                    self._semaphore.release()


def _path_allowed(path: str, policy: HostPolicy) -> bool:
    if not policy.allow_suffix:
        return path == policy.path_prefix
    if not path.startswith(policy.path_prefix):
        return False
    suffix = path.removeprefix(policy.path_prefix)
    return bool(suffix) and "/" not in suffix and "\\" not in suffix


def _path_characters_safe(path: str) -> bool:
    return all(character.isascii() and (character.isalnum() or character in "/._=,-") for character in path)


def _json_content_type_allowed(content_type: str | None) -> bool:
    if content_type is None:
        return False
    media_type, *parameters = content_type.split(";")
    normalized_media_type = media_type.strip().lower()
    subtype = normalized_media_type.removeprefix("application/")
    if normalized_media_type != "application/json" and not (
        normalized_media_type.startswith("application/") and len(subtype) > len("+json") and subtype.endswith("+json")
    ):
        return False
    charsets: list[str] = []
    for parameter in parameters:
        name, separator, value = parameter.partition("=")
        if name.strip().lower() != "charset":
            continue
        if not separator:
            return False
        charsets.append(value.strip().strip('"').lower())
    return all(charset in {"utf-8", "utf8"} for charset in charsets)


def _decode_json(payload: bytes, content_type: str | None) -> object:
    if not _json_content_type_allowed(content_type):
        raise ValueError("invalid JSON content type")
    text = payload.decode("utf-8", errors="strict")
    _validate_json_nesting(text)
    return json.loads(
        text,
        parse_float=Decimal,
        parse_int=int,
        parse_constant=_reject_json_constant,
    )


def _validate_json_nesting(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_JSON_NESTING:
                raise ValueError("JSON nesting limit exceeded")
        elif character in "]}":
            depth -= 1


def _reject_json_constant(_: str) -> Any:
    raise ValueError("non-finite JSON number")
