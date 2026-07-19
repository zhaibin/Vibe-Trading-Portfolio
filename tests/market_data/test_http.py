import asyncio
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from urllib.parse import urlsplit

import httpx
import pytest

from vibe_portfolio.config import Settings
from vibe_portfolio.market_data import http as market_http
from vibe_portfolio.market_data.http import BoundedProviderHttp
from vibe_portfolio.market_data.models import ProviderErrorCode, ProviderFailure

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/DEMO"


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class BlockingByteStream(httpx.AsyncByteStream):
    def __init__(
        self,
        *,
        on_start: Callable[[], None],
        release: asyncio.Event,
    ) -> None:
        self._on_start = on_start
        self._release = release

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self._on_start()
        await self._release.wait()
        yield b"{}"


class CloseTrackingByteStream(httpx.AsyncByteStream):
    def __init__(self, started: asyncio.Event, release: asyncio.Event, closed: asyncio.Event) -> None:
        self._started = started
        self._release = release
        self._closed = closed

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self._started.set()
        await self._release.wait()
        yield b"{}"

    async def aclose(self) -> None:
        self._closed.set()


class ExplodingByteStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise RuntimeError("provider-stream-secret")
        yield b""  # pragma: no cover


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.value += delay


class BlockingClock(FakeClock):
    def __init__(self) -> None:
        super().__init__()
        self.sleep_started = asyncio.Event()
        self.release_sleep = asyncio.Event()

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.sleep_started.set()
        await self.release_sleep.wait()
        self.value += delay


def bounded(
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]],
    *,
    allowed_hosts: set[str] | None = None,
    settings: Settings | None = None,
    clock: FakeClock | None = None,
) -> BoundedProviderHttp:
    fake_clock = clock or FakeClock()
    return BoundedProviderHttp(
        allowed_hosts=allowed_hosts or {"query1.finance.yahoo.com"},
        transport=httpx.MockTransport(handler),
        settings=settings or Settings(_env_file=None),
        monotonic=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://query1.finance.yahoo.com/v8/finance/chart/DEMO",
        "https://attacker.example/v8/finance/chart/DEMO",
        "https://user@query1.finance.yahoo.com/v8/finance/chart/DEMO",
        "https://query1.finance.yahoo.com:443/v8/finance/chart/DEMO",
        "https://127.0.0.1/v8/finance/chart/DEMO",
        "https://QUERY1.finance.yahoo.com/v8/finance/chart/DEMO",
        "https://query1.finance.yahoo.com/not-allowed",
        "https://query1.finance.yahoo.com/v8/finance/chart/DEMO#fragment",
        " https://query1.finance.yahoo.com/v8/finance/chart/DEMO",
        "https://query1.finance.yahoo.com/v8/finance/chart/DEMO?q=DE MO",
    ],
)
async def test_transport_rejects_non_exact_destination_without_request(url: str) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    transport = bounded(handler)
    with pytest.raises(ProviderFailure, match="PROVIDER_DESTINATION_BLOCKED") as raised:
        await transport.get_json(url)

    assert raised.value.code is ProviderErrorCode.DESTINATION_BLOCKED
    assert calls == 0


@pytest.mark.parametrize(
    "path",
    [
        "/v8/finance/chart/../secret",
        "/v8/finance/chart/%2e%2e/secret",
        "/v8/finance/chart/%2E%2E%2fsecret",
        "/v8/finance/chart/%2fsecret",
        "/v8/finance/chart/%5csecret",
        "/v8/finance/chart/%252e%252e%252fsecret",
        "/v8/finance/chart/..\\secret",
        "/v8/finance/chart/DE MO",
        "/v8/finance/chart/ＤＥＭＯ",
    ],
)
async def test_transport_rejects_path_normalization_ambiguity_before_request(path: str) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(ProviderFailure, match="PROVIDER_DESTINATION_BLOCKED"):
        await bounded(handler).get_json(f"https://query1.finance.yahoo.com{path}")

    assert calls == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://query2.finance.yahoo.com/v1/finance/search/extra?q=DEMO",
        "https://searchapi.eastmoney.com/api/suggest/get/extra?input=DEMO",
        "https://push2.eastmoney.com/api/qt/stock/get/extra?secid=1.000001",
    ],
)
async def test_transport_rejects_suffix_on_exact_endpoint_routes(url: str) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    host = urlsplit(url).hostname
    assert host is not None
    with pytest.raises(ProviderFailure, match="PROVIDER_DESTINATION_BLOCKED"):
        await bounded(handler, allowed_hosts={host}).get_json(url)

    assert calls == 0


async def test_transport_allows_path_like_bytes_only_in_query() -> None:
    seen_query: bytes | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.query
        return httpx.Response(200, json={})

    await bounded(handler).get_json(YAHOO_URL + "?q=..%2F%5Csecret")

    assert seen_query == b"q=..%2F%5Csecret"


async def test_transport_sanitizes_request_construction_errors_before_io() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(ProviderFailure, match="PROVIDER_DESTINATION_BLOCKED") as raised:
        await bounded(handler).get_json(YAHOO_URL + "?q=\ud800")

    assert calls == 0
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_transport_rejects_non_code_defined_allowed_host() -> None:
    with pytest.raises(ValueError, match="allowed_hosts"):
        bounded(lambda _: httpx.Response(200), allowed_hosts={"attacker.example"})


async def test_transport_rejects_arbitrary_injected_http_client() -> None:
    client = httpx.AsyncClient()
    try:
        with pytest.raises(ValueError, match="MockTransport"):
            BoundedProviderHttp(allowed_hosts={"query1.finance.yahoo.com"}, client=client)
    finally:
        await client.aclose()


async def test_mock_client_defaults_auth_and_cookies_never_reach_request() -> None:
    seen_headers: httpx.Headers | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_headers
        seen_headers = request.headers
        return httpx.Response(200, headers={"set-cookie": "provider_session=response-secret"}, json={})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={
            "Authorization": "Bearer header-secret",
            "X-Private": "private-header",
            "User-Agent": "unsafe-agent",
            "Accept": "*/*",
        },
        cookies={"request_session": "cookie-secret"},
        auth=("private-user", "private-password"),
    )
    transport = BoundedProviderHttp(
        allowed_hosts={"query1.finance.yahoo.com"},
        client=client,
        settings=Settings(_env_file=None),
    )

    await transport.get_json(YAHOO_URL)

    assert seen_headers is not None
    assert seen_headers["user-agent"] == "Vibe-Trading-Portfolio/0.1 market-data"
    assert seen_headers["accept"] == "application/json, application/*+json"
    assert set(seen_headers) == {"host", "user-agent", "accept"}
    assert "authorization" not in seen_headers
    assert "x-private" not in seen_headers
    assert "cookie" not in seen_headers
    assert list(transport._client.cookies.jar) == []  # noqa: SLF001


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (302, ProviderErrorCode.DESTINATION_BLOCKED),
        (404, ProviderErrorCode.RESPONSE_INVALID),
        (503, ProviderErrorCode.RESPONSE_INVALID),
    ],
)
async def test_transport_maps_redirects_and_status_without_leaking_location(
    status: int, expected: ProviderErrorCode
) -> None:
    transport = bounded(
        lambda _: httpx.Response(status, headers={"location": "https://attacker.example/?secret=value"})
    )

    with pytest.raises(ProviderFailure) as raised:
        await transport.get_json(YAHOO_URL + "?private=query")

    assert raised.value.code is expected
    assert raised.value.args == (expected.value,)
    assert "secret" not in str(raised.value)
    assert "private" not in str(raised.value)


async def test_transport_maps_httpx_timeout_without_exception_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider exposed secret", request=request)

    with pytest.raises(ProviderFailure, match="PROVIDER_TIMEOUT") as raised:
        await bounded(handler).get_json(YAHOO_URL)

    assert raised.value.code is ProviderErrorCode.TIMEOUT
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert "secret" not in "".join(traceback.format_exception(raised.value))


async def test_transport_sanitizes_unexpected_stream_exception() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        stream=ExplodingByteStream(),
    )

    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID") as raised:
        await bounded(lambda _: response).get_json(YAHOO_URL)

    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert "provider-stream-secret" not in "".join(traceback.format_exception(raised.value))


async def test_transport_enforces_total_operation_timeout() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={})

    settings = Settings(_env_file=None, market_operation_timeout_seconds=0.01)
    with pytest.raises(ProviderFailure, match="PROVIDER_TIMEOUT"):
        await bounded(handler, settings=settings).get_json(YAHOO_URL)


async def test_transport_total_timeout_includes_blocking_json_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    original_loads = market_http.json.loads
    parse_calls = 0

    def slow_loads(*args: object, **kwargs: object) -> object:
        nonlocal parse_calls
        parse_calls += 1
        if parse_calls == 1:
            time.sleep(0.05)
        return original_loads(*args, **kwargs)

    monkeypatch.setattr(market_http.json, "loads", slow_loads)
    settings = Settings(_env_file=None, market_operation_timeout_seconds=0.01)

    with pytest.raises(ProviderFailure, match="PROVIDER_TIMEOUT"):
        await bounded(lambda _: httpx.Response(200, json={}), settings=settings).get_json(YAHOO_URL)


async def test_cancel_during_json_parse_is_responsive(monkeypatch: pytest.MonkeyPatch) -> None:
    original_loads = market_http.json.loads
    parse_calls = 0

    def slow_first_loads(*args: object, **kwargs: object) -> object:
        nonlocal parse_calls
        parse_calls += 1
        if parse_calls == 1:
            time.sleep(0.05)
        return original_loads(*args, **kwargs)

    monkeypatch.setattr(market_http.json, "loads", slow_first_loads)
    transport = bounded(lambda _: httpx.Response(200, json={}))
    task = asyncio.create_task(transport.get_json(YAHOO_URL))
    asyncio.get_running_loop().call_later(0.005, task.cancel)

    with pytest.raises(asyncio.CancelledError):
        await task

    assert await transport.get_json(YAHOO_URL) == {}


async def test_transport_spaces_same_host_starts_with_injected_clock() -> None:
    clock = FakeClock()
    transport = bounded(lambda _: httpx.Response(200, json={}), clock=clock)

    await transport.get_json(YAHOO_URL)
    await transport.get_json(YAHOO_URL)

    assert clock.sleeps == [pytest.approx(0.6)]


async def test_transport_never_sends_provider_cookies() -> None:
    cookie_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cookie_headers.append(request.headers.get("cookie"))
        return httpx.Response(200, headers={"set-cookie": "provider_session=secret"}, json={})

    transport = bounded(handler)
    await transport.get_json(YAHOO_URL)
    await transport.get_json(YAHOO_URL)

    assert cookie_headers == [None, None]
    assert list(transport._client.cookies.jar) == []  # noqa: SLF001


async def test_cancel_during_response_headers_releases_concurrency_slot() -> None:
    calls = 0
    header_started = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            header_started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={})

    settings = Settings(_env_file=None, market_max_concurrency=1)
    transport = bounded(handler, settings=settings)
    task = asyncio.create_task(transport.get_json(YAHOO_URL))
    await asyncio.wait_for(header_started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert await asyncio.wait_for(transport.get_json(YAHOO_URL), timeout=1) == {}


async def test_cancel_during_response_body_closes_response_and_releases_slot() -> None:
    body_started = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=CloseTrackingByteStream(body_started, release, closed),
            )
        return httpx.Response(200, json={})

    settings = Settings(_env_file=None, market_max_concurrency=1)
    transport = bounded(handler, settings=settings)
    task = asyncio.create_task(transport.get_json(YAHOO_URL))
    await asyncio.wait_for(body_started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert closed.is_set()
    assert await asyncio.wait_for(transport.get_json(YAHOO_URL), timeout=1) == {}


async def test_cancel_during_spacing_does_not_advance_last_start() -> None:
    clock = BlockingClock()
    transport = bounded(lambda _: httpx.Response(200, json={}), clock=clock)
    await transport.get_json(YAHOO_URL)
    cancelled = asyncio.create_task(transport.get_json(YAHOO_URL))
    await asyncio.wait_for(clock.sleep_started.wait(), timeout=1)
    cancelled.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cancelled

    third = asyncio.create_task(transport.get_json(YAHOO_URL))
    await asyncio.sleep(0)
    assert clock.sleeps == [pytest.approx(0.6), pytest.approx(0.6)]
    clock.release_sleep.set()
    assert await third == {}


async def test_transport_caps_global_concurrency() -> None:
    active = 0
    peak = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_started.set()
        await release.wait()
        active -= 1
        return httpx.Response(200, json={})

    settings = Settings(_env_file=None, market_max_concurrency=2)
    transport = bounded(
        handler,
        settings=settings,
        allowed_hosts={"query1.finance.yahoo.com", "query2.finance.yahoo.com", "searchapi.eastmoney.com"},
    )
    tasks = [
        asyncio.create_task(transport.get_json(YAHOO_URL)),
        asyncio.create_task(transport.get_json("https://query2.finance.yahoo.com/v1/finance/search?q=DEMO")),
        asyncio.create_task(transport.get_json("https://searchapi.eastmoney.com/api/suggest/get?input=DEMO")),
    ]
    await asyncio.wait_for(two_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert peak == 2
    release.set()
    await asyncio.gather(*tasks)


async def test_transport_caps_concurrency_until_stream_body_is_consumed() -> None:
    streams_started = 0
    two_started = asyncio.Event()
    third_started = asyncio.Event()
    release = asyncio.Event()

    def on_stream_start() -> None:
        nonlocal streams_started
        streams_started += 1
        if streams_started == 2:
            two_started.set()
        if streams_started == 3:
            third_started.set()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=BlockingByteStream(on_start=on_stream_start, release=release),
        )

    settings = Settings(_env_file=None, market_max_concurrency=2)
    transport = bounded(
        handler,
        settings=settings,
        allowed_hosts={"query1.finance.yahoo.com", "query2.finance.yahoo.com", "searchapi.eastmoney.com"},
    )
    tasks = [
        asyncio.create_task(transport.get_json(YAHOO_URL)),
        asyncio.create_task(transport.get_json("https://query2.finance.yahoo.com/v1/finance/search?q=DEMO")),
        asyncio.create_task(transport.get_json("https://searchapi.eastmoney.com/api/suggest/get?input=DEMO")),
    ]
    await asyncio.wait_for(two_started.wait(), timeout=1)
    await asyncio.sleep(0)

    assert not third_started.is_set()

    release.set()
    await asyncio.gather(*tasks)


async def test_transport_rejects_declared_and_streamed_oversize_responses() -> None:
    settings = Settings(_env_file=None, market_max_response_bytes=1024)
    declared = bounded(
        lambda _: httpx.Response(200, headers={"content-length": "1025"}, content=b"{}"),
        settings=settings,
    )
    streamed = bounded(
        lambda _: httpx.Response(200, stream=ByteStream(b"x" * 700, b"y" * 400)),
        settings=settings,
    )

    for transport in (declared, streamed):
        with pytest.raises(ProviderFailure, match="PROVIDER_RESPONSE_TOO_LARGE") as raised:
            await transport.get_json(YAHOO_URL)
        assert raised.value.code is ProviderErrorCode.RESPONSE_TOO_LARGE


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, headers={"content-type": "application/json"}, content=b"\xff"),
        httpx.Response(200, headers={"content-type": "application/json"}, content=b"{"),
        httpx.Response(200, headers={"content-type": "application/json"}, content=b'{"value": NaN}'),
        httpx.Response(200, headers={"content-length": "invalid"}, content=b"{}"),
    ],
)
async def test_transport_rejects_invalid_utf8_json_constants_and_length(response: httpx.Response) -> None:
    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID") as raised:
        await bounded(lambda _: response).get_json(YAHOO_URL)

    assert raised.value.code is ProviderErrorCode.RESPONSE_INVALID


@pytest.mark.parametrize(
    "content_type",
    [
        None,
        "text/plain",
        "application/octet-stream",
        "application/+json",
        "application/json; charset=utf-8; charset=iso-8859-1",
    ],
)
async def test_json_rejects_missing_wrong_or_conflicting_media_type(content_type: str | None) -> None:
    headers = {} if content_type is None else {"content-type": content_type}
    response = httpx.Response(200, headers=headers, content=b"{}")

    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID"):
        await bounded(lambda _: response).get_json(YAHOO_URL)


async def test_json_accepts_structured_json_media_type() -> None:
    response = httpx.Response(200, headers={"content-type": "application/problem+json"}, content=b"{}")

    assert await bounded(lambda _: response).get_json(YAHOO_URL) == {}


async def test_json_rejects_excessive_nesting() -> None:
    body = b"[" * 65 + b"0" + b"]" * 65
    response = httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID"):
        await bounded(lambda _: response).get_json(YAHOO_URL)


async def test_json_nesting_scan_ignores_brackets_inside_strings() -> None:
    body = ('{"value":"' + "[" * 65 + '"}').encode()
    response = httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    assert await bounded(lambda _: response).get_json(YAHOO_URL) == {"value": "[" * 65}


@pytest.mark.parametrize("error", [RecursionError("body-secret"), MemoryError("body-secret")])
async def test_json_resource_errors_are_sanitized(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_loads(*_: object, **__: object) -> object:
        raise error

    monkeypatch.setattr(market_http.json, "loads", fail_loads)

    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID") as raised:
        await bounded(lambda _: httpx.Response(200, json={})).get_json(YAHOO_URL)

    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert "body-secret" not in "".join(traceback.format_exception(raised.value))


async def test_json_numbers_decode_as_decimal_without_binary_float() -> None:
    transport = bounded(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"price": 0.1, "count": 2}',
        )
    )

    result = await transport.get_json(YAHOO_URL)

    assert result == {"price": Decimal("0.1"), "count": 2}
    assert isinstance(result["price"], Decimal)  # type: ignore[index]


async def test_json_rejects_declared_non_utf8_charset() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json; charset=iso-8859-1"},
        content=b'{"value": "ascii"}',
    )

    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID"):
        await bounded(lambda _: response).get_json(YAHOO_URL)


async def test_text_uses_only_fixed_gb18030_and_rejects_invalid_bytes_or_encoding() -> None:
    valid = "示例~1.25".encode("gb18030")
    transport = bounded(lambda _: httpx.Response(200, content=valid))
    assert await transport.get_text(YAHOO_URL, encoding="gb18030") == "示例~1.25"

    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID"):
        await transport.get_text(YAHOO_URL, encoding="utf-8")
    invalid = bounded(lambda _: httpx.Response(200, content=b"\x81"))
    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID"):
        await invalid.get_text(YAHOO_URL, encoding="gb18030")
