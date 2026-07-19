"""Injected FastAPI router for bounded search and explicit quote refresh."""

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header
from pydantic import BaseModel

from vibe_portfolio.market_data.models import InstrumentCandidate, RefreshScope
from vibe_portfolio.market_data.service import (
    MarketDataService,
    MarketSearchUnavailable,
    RefreshInProgress,
    RefreshRunDetails,
    RefreshRunNotFound,
    RefreshValidationError,
    SearchValidationError,
)
from vibe_portfolio.portfolio.database import DatabaseBusyError, DatabaseStartupError
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.repository import RepositoryError
from vibe_portfolio.portfolio.router import PortfolioRoute, api_error
from vibe_portfolio.portfolio.schemas import ErrorEnvelope, RefreshItemView, RefreshRequest, RefreshRunView

_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{8,128}$")


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


def _refresh_view(details: RefreshRunDetails) -> RefreshRunView:
    run = details.run
    status = "succeeded" if run.status == "completed" else run.status
    return RefreshRunView(
        run_id=UUID(run.id),
        status=status,  # type: ignore[arg-type]
        updated=run.updated_count,
        stale=run.stale_count,
        unavailable=run.unavailable_count,
        providers_used=tuple(sorted({item.provider for item in details.items if item.provider is not None})),
        started_at=run.started_at,
        finished_at=run.finished_at,
        items=[
            RefreshItemView(
                instrument_id=UUID(item.instrument_id),
                outcome=item.outcome,  # type: ignore[arg-type]
                provider=item.provider,
                error_code=item.error_code,
            )
            for item in details.items
        ],
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

    @router.post(
        "/market-data/refresh",
        response_model=RefreshRunView,
        responses={409: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}, 502: {"model": ErrorEnvelope}},
    )
    async def refresh_quotes(
        command: RefreshRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> object:
        if idempotency_key is None or _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
            return api_error("VALIDATION_ERROR", 422, {"Idempotency-Key": "invalid"})
        scope = RefreshScope(instrument_ids=None if command.instrument_ids is None else tuple(command.instrument_ids))
        try:
            result = await service.refresh(scope, idempotency_key)
            view = _refresh_view(await service.refresh_run(result.run_id))
            if result.status == "failed":
                return api_error("QUOTE_UNAVAILABLE", 502, {"run": view.model_dump(mode="json")})
            return view
        except RefreshValidationError as error:
            return api_error("VALIDATION_ERROR", 422, {error.field: "invalid"})
        except RefreshInProgress as error:
            fields: dict[str, object] | None = None if error.run_id is None else {"run_id": str(error.run_id)}
            return api_error("QUOTE_REFRESH_IN_PROGRESS", 409, fields)
        except RepositoryError as error:
            status = 409 if error.code == "IDEMPOTENCY_CONFLICT" else 503
            return api_error(error.code, status, error.fields)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("QUOTE_UNAVAILABLE", 502)

    @router.get(
        "/market-data/refresh/{run_id}",
        response_model=RefreshRunView,
        responses={404: {"model": ErrorEnvelope}},
    )
    async def get_refresh_run(run_id: UUID) -> object:
        try:
            return _refresh_view(await service.refresh_run(run_id))
        except RefreshRunNotFound:
            return api_error("QUOTE_REFRESH_NOT_FOUND", 404)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    return router
