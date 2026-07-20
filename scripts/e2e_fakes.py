"""Deterministic in-process market-data adapters used only by the E2E server."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from vibe_portfolio.market_data.models import (
    InstrumentCandidate,
    ProviderErrorCode,
    ProviderFailure,
    ProviderInstrument,
    ProviderQuote,
    ProviderSymbol,
)
from vibe_portfolio.market_data.service import ProviderRegistry
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market


class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self._quote_calls: dict[str, int] = {}

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]:
        normalized = query.upper()
        candidates: list[InstrumentCandidate] = []
        if self.name == "eastmoney" and normalized in {"600000", "600000.SH", "浦发银行"}:
            candidates.append(
                InstrumentCandidate(
                    canonical_symbol="600000.SH",
                    name="浦发银行",
                    market=Market.CN_SH,
                    currency=Currency.CNY,
                    asset_type=AssetType.EQUITY,
                    provider_symbols=(ProviderSymbol("eastmoney", "1.600000"),),
                )
            )
        if self.name == "yahoo" and normalized in {"00700", "00700.HK", "腾讯控股"}:
            candidates.append(
                InstrumentCandidate(
                    canonical_symbol="00700.HK",
                    name="腾讯控股",
                    market=Market.HK,
                    currency=Currency.HKD,
                    asset_type=AssetType.EQUITY,
                    provider_symbols=(ProviderSymbol("yahoo", "0700.HK"),),
                )
            )
        if self.name == "yahoo" and normalized in {"DEMO", "DEMO.US", "DEMO CORP"}:
            candidates.append(
                InstrumentCandidate(
                    canonical_symbol="DEMO.US",
                    name="Demo Corp",
                    market=Market.US,
                    currency=Currency.USD,
                    asset_type=AssetType.EQUITY,
                    provider_symbols=(ProviderSymbol("yahoo", "DEMO"),),
                )
            )
        return candidates[:limit]

    async def fetch_quotes(
        self, instruments: Sequence[ProviderInstrument]
    ) -> list[ProviderQuote]:
        quotes: list[ProviderQuote] = []
        for instrument in instruments:
            calls = self._quote_calls.get(instrument.canonical_symbol, 0) + 1
            self._quote_calls[instrument.canonical_symbol] = calls
            if instrument.canonical_symbol == "600000.SH" and self.name == "eastmoney" and calls == 1:
                price = Decimal("10")
            elif instrument.canonical_symbol == "00700.HK" and self.name == "yahoo":
                price = Decimal("400")
            else:
                raise ProviderFailure(ProviderErrorCode.TIMEOUT)
            quotes.append(
                ProviderQuote(
                    canonical_symbol=instrument.canonical_symbol,
                    provider_symbol=instrument.provider_symbol,
                    price=price,
                    currency=instrument.currency,
                    as_of=datetime.now(UTC),
                    provider=self.name,
                )
            )
        return quotes


def build_e2e_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        (
            _FakeProvider("eastmoney"),
            _FakeProvider("yahoo"),
            _FakeProvider("tencent"),
        )
    )
