from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from vibe_portfolio.market_data.models import InstrumentCandidate, ProviderInstrument, ProviderQuote
from vibe_portfolio.market_data.service import ProviderRegistry


class ProbeProvider:
    def __init__(self, name: str, *, corrupt: bool = False) -> None:
        self.name = name
        self.corrupt = corrupt
        self.calls: list[tuple[str, ...]] = []

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        return []

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]:
        self.calls.append(tuple(item.canonical_symbol for item in instruments))
        return [
            ProviderQuote(
                canonical_symbol=instrument.canonical_symbol,
                provider_symbol=instrument.provider_symbol,
                price=Decimal("-1") if self.corrupt and index == 0 else Decimal("12.345678"),
                currency=instrument.currency,
                as_of=datetime.now(UTC),
                provider=self.name,
            )
            for index, instrument in enumerate(instruments)
        ]


async def test_public_fixture_probe_is_bounded_and_reports_each_provider_independently() -> None:
    eastmoney = ProbeProvider("eastmoney")
    yahoo = ProbeProvider("yahoo", corrupt=True)
    tencent = ProbeProvider("tencent")
    registry = ProviderRegistry((eastmoney, yahoo, tencent))

    result = await registry.probe_public_fixtures(("510300.SH", "00700.HK", "AAPL.US"))

    assert result.passed is False
    assert result.fixtures == ("510300.SH", "00700.HK", "AAPL.US")
    by_provider = {item.provider: item for item in result.providers}
    assert by_provider["eastmoney"].passed is True
    assert by_provider["eastmoney"].fixtures == ("510300.SH", "00700.HK")
    assert by_provider["yahoo"].passed is False
    assert by_provider["yahoo"].errors == ("QUOTE_RESPONSE_INVALID",)
    assert by_provider["tencent"].passed is True
    assert eastmoney.calls == [("510300.SH", "00700.HK")]
    assert yahoo.calls == [("00700.HK", "AAPL.US")]
    assert tencent.calls == [("510300.SH",)]


async def test_public_fixture_probe_rejects_any_non_reviewed_fixture_set() -> None:
    registry = ProviderRegistry(
        (ProbeProvider("eastmoney"), ProbeProvider("yahoo"), ProbeProvider("tencent"))
    )

    try:
        await registry.probe_public_fixtures(("MSFT.US",))
    except ValueError as error:
        assert str(error) == "public probe fixtures must match the reviewed allowlist"
    else:
        raise AssertionError("unreviewed fixture set was accepted")
