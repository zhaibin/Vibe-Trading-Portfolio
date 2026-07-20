import os

import pytest

from vibe_portfolio.config import Settings
from vibe_portfolio.market_data.service import build_live_provider_registry


@pytest.mark.market_contract
async def test_enabled_live_market_providers_return_valid_public_quotes() -> None:
    if os.environ.get("PORTFOLIO_RUN_MARKET_CONTRACT") != "1":
        pytest.skip("PORTFOLIO_RUN_MARKET_CONTRACT=1 is not set; market contract not run")
    registry = build_live_provider_registry(Settings(_env_file=None))
    try:
        result = await registry.probe_public_fixtures(("510300.SH", "00700.HK", "AAPL.US"))
        assert result.passed, result.model_dump_json(indent=2)
    finally:
        await registry.close()
