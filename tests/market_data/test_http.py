import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal

import httpx
import pytest

from vibe_portfolio.config import Settings
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


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.value += delay


def client_for(handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


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
        client=client_for(handler),
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


def test_transport_rejects_non_code_defined_allowed_host() -> None:
    with pytest.raises(ValueError, match="allowed_hosts"):
        bounded(lambda _: httpx.Response(200), allowed_hosts={"attacker.example"})


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


async def test_transport_enforces_total_operation_timeout() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={})

    settings = Settings(_env_file=None, market_operation_timeout_seconds=0.01)
    with pytest.raises(ProviderFailure, match="PROVIDER_TIMEOUT"):
        await bounded(handler, settings=settings).get_json(YAHOO_URL)


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
        return httpx.Response(200, stream=BlockingByteStream(on_start=on_stream_start, release=release))

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
        httpx.Response(200, content=b"\xff"),
        httpx.Response(200, content=b"{"),
        httpx.Response(200, content=b'{"value": NaN}'),
        httpx.Response(200, headers={"content-length": "invalid"}, content=b"{}"),
    ],
)
async def test_transport_rejects_invalid_utf8_json_constants_and_length(response: httpx.Response) -> None:
    with pytest.raises(ProviderFailure, match="QUOTE_RESPONSE_INVALID") as raised:
        await bounded(lambda _: response).get_json(YAHOO_URL)

    assert raised.value.code is ProviderErrorCode.RESPONSE_INVALID


async def test_json_numbers_decode_as_decimal_without_binary_float() -> None:
    transport = bounded(lambda _: httpx.Response(200, content=b'{"price": 0.1, "count": 2}'))

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
