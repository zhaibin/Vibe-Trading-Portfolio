"""Injected FastAPI router for bounded instrument search."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from vibe_portfolio.market_data.models import InstrumentCandidate
from vibe_portfolio.market_data.service import MarketDataService, MarketSearchUnavailable, SearchValidationError
from vibe_portfolio.portfolio.database import DatabaseBusyError, DatabaseStartupError
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.router import PortfolioRoute, api_error
from vibe_portfolio.portfolio.schemas import ErrorEnvelope


class InstrumentSearchView(BaseModel):
    candidate_id: UUID
    canonical_symbol: str
    name: str
    market: Market
    currency: Currency
    asset_type: AssetType
    sources: tuple[str, ...]


def _view(candidate: InstrumentCandidate) -> InstrumentSearchView:
    assert candidate.candidate_id is not None
    return InstrumentSearchView(
        candidate_id=candidate.candidate_id,
        canonical_symbol=candidate.canonical_symbol,
        name=candidate.name,
        market=candidate.market,
        currency=candidate.currency,
        asset_type=candidate.asset_type,
        sources=candidate.sources,
    )


def build_market_data_router(service: MarketDataService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["market-data"], route_class=PortfolioRoute)

    @router.get(
        "/instruments/search",
        response_model=list[InstrumentSearchView],
        responses={422: {"model": ErrorEnvelope}, 503: {"model": ErrorEnvelope}},
    )
    async def search_instruments(q: str, limit: int = 10) -> object:
        try:
            return [_view(candidate) for candidate in await service.search(q, limit)]
        except SearchValidationError as error:
            return api_error("VALIDATION_ERROR", 422, {error.field: "invalid"})
        except MarketSearchUnavailable:
            return api_error("MARKET_SEARCH_UNAVAILABLE", 503)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("MARKET_SEARCH_UNAVAILABLE", 503)

    return router
