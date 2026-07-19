"""Fixed-host, rate-spaced, size-bounded provider HTTP transport."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import SplitResult, urlsplit

import httpx

from vibe_portfolio.config import Settings
from vibe_portfolio.market_data.models import ProviderErrorCode, ProviderFailure

_USER_AGENT: Final = "Vibe-Trading-Portfolio/0.1 market-data"


@dataclass(frozen=True, slots=True)
class HostPolicy:
    path_prefix: str
    minimum_interval_seconds: float


HOST_POLICIES = MappingProxyType(
    {
        "searchapi.eastmoney.com": HostPolicy("/api/suggest/get", 1.0),
        "push2.eastmoney.com": HostPolicy("/api/qt/stock/get", 1.0),
        "query1.finance.yahoo.com": HostPolicy("/v8/finance/chart/", 0.6),
        "query2.finance.yahoo.com": HostPolicy("/v1/finance/search", 0.6),
        "qt.gtimg.cn": HostPolicy("/q=", 0.5),
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
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
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
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "BoundedProviderHttp":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def get_json(self, url: str) -> object:
        payload, content_type = await self._get_bytes(url)
        try:
            if not _json_charset_allowed(content_type):
                raise ValueError("JSON charset must be UTF-8")
            text = payload.decode("utf-8", errors="strict")
            return json.loads(
                text,
                parse_float=Decimal,
                parse_int=int,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from error

    async def get_text(self, url: str, *, encoding: str = "gb18030") -> str:
        if encoding != "gb18030":
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID)
        payload, _ = await self._get_bytes(url)
        try:
            return payload.decode("gb18030", errors="strict")
        except UnicodeDecodeError as error:
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from error

    async def _get_bytes(self, url: str) -> tuple[bytes, str | None]:
        host = self._validate_destination(url)
        try:
            async with asyncio.timeout(self.settings.market_operation_timeout_seconds):
                return await self._send_bounded(url, host)
        except ProviderFailure:
            raise
        except (TimeoutError, httpx.TimeoutException) as error:
            raise ProviderFailure(ProviderErrorCode.TIMEOUT) from error
        except httpx.HTTPError as error:
            raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from error

    def _validate_destination(self, url: str) -> str:
        try:
            destination = urlsplit(url)
            host = destination.hostname
            port = destination.port
        except ValueError as error:
            raise ProviderFailure(ProviderErrorCode.DESTINATION_BLOCKED) from error
        if (
            destination.scheme != "https"
            or host is None
            or destination.netloc != host
            or destination.username is not None
            or destination.password is not None
            or port is not None
            or destination.fragment
            or host not in self._allowed_hosts
            or not _path_allowed(destination, HOST_POLICIES[host])
        ):
            raise ProviderFailure(ProviderErrorCode.DESTINATION_BLOCKED)
        return host

    async def _send_bounded(self, url: str, host: str) -> tuple[bytes, str | None]:
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
                request = self._client.build_request("GET", url)
                request.headers.pop("cookie", None)
                response = await self._client.send(request, stream=True, follow_redirects=False)
            if 300 <= response.status_code < 400:
                raise ProviderFailure(ProviderErrorCode.DESTINATION_BLOCKED)
            if not 200 <= response.status_code < 300:
                raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID)
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    parsed_length = int(declared_length)
                except ValueError as error:
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID) from error
                if parsed_length < 0:
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_INVALID)
                if parsed_length > self.settings.market_max_response_bytes:
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_TOO_LARGE)
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > self.settings.market_max_response_bytes:
                    raise ProviderFailure(ProviderErrorCode.RESPONSE_TOO_LARGE)
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type")
        finally:
            try:
                if response is not None:
                    await response.aclose()
            finally:
                if slot_acquired:
                    self._semaphore.release()


def _path_allowed(destination: SplitResult, policy: HostPolicy) -> bool:
    return destination.path.startswith(policy.path_prefix)


def _json_charset_allowed(content_type: str | None) -> bool:
    if content_type is None:
        return True
    for parameter in content_type.split(";")[1:]:
        name, separator, value = parameter.partition("=")
        if name.strip().lower() != "charset":
            continue
        if not separator:
            return False
        return value.strip().strip('"').lower() in {"utf-8", "utf8"}
    return True


def _reject_json_constant(_: str) -> Any:
    raise ValueError("non-finite JSON number")
