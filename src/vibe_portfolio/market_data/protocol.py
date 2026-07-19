"""Protocols implemented by independent market-data adapters."""

from collections.abc import Sequence
from typing import Protocol

from vibe_portfolio.market_data.models import InstrumentCandidate, ProviderInstrument, ProviderQuote


class MarketDataProvider(Protocol):
    name: str

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]: ...

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]: ...
