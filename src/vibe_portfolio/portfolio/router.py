"""Injected FastAPI router for local portfolio resources."""

import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from vibe_portfolio.portfolio.database import DatabaseBusyError, DatabaseStartupError
from vibe_portfolio.portfolio.domain import AssetType, Currency, Market
from vibe_portfolio.portfolio.repository import RepositoryError
from vibe_portfolio.portfolio.schemas import (
    AccountCreate,
    AccountPatch,
    AccountView,
    CursorPage,
    ErrorEnvelope,
    InstrumentConfirm,
    InstrumentView,
    PortfolioSummary,
    PositionCreate,
    PositionPatch,
    PositionView,
)
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import AccountRow, InstrumentRow, PositionRow

_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{8,128}$")


def api_error(code: str, status: int, fields: dict[str, object] | None = None) -> JSONResponse:
    detail: dict[str, object] = {"code": code}
    if fields is not None:
        detail["fields"] = fields
    return JSONResponse(status_code=status, content={"error": detail})


def _account_view(account: AccountRow) -> AccountView:
    return AccountView(
        id=account.id,
        name=account.name,
        currency=Currency(account.currency),
        cash_balance=account.cash_balance,
        version=account.version,
        created_at=account.created_at,
        updated_at=account.updated_at,
        archived_at=account.archived_at,
    )


def _instrument_view(instrument: InstrumentRow) -> InstrumentView:
    return InstrumentView(
        id=instrument.id,
        canonical_symbol=instrument.canonical_symbol,
        name=instrument.name,
        market=Market(instrument.market),
        currency=Currency(instrument.currency),
        asset_type=AssetType(instrument.asset_type),
        created_at=instrument.created_at,
        updated_at=instrument.updated_at,
    )


def _position_view(position: PositionRow) -> PositionView:
    return PositionView(
        id=position.id,
        account_id=position.account_id,
        instrument_id=position.instrument_id,
        quantity=position.quantity,
        average_cost=position.average_cost,
        note=position.note,
        version=position.version,
        created_at=position.created_at,
        updated_at=position.updated_at,
        archived_at=position.archived_at,
    )


def _key_or_error(key: str | None) -> JSONResponse | str:
    if key is None or _IDEMPOTENCY_KEY.fullmatch(key) is None:
        return api_error("VALIDATION_ERROR", 422, {"Idempotency-Key": "invalid"})
    return key


def _repository_error(error: RepositoryError) -> JSONResponse:
    status_by_code = {
        "IDEMPOTENCY_CONFLICT": 409,
        "DUPLICATE_ACCOUNT_NAME": 409,
        "CONCURRENT_MODIFICATION": 409,
        "ACCOUNT_HAS_ACTIVE_POSITIONS": 409,
        "DUPLICATE_POSITION": 409,
        "POSITION_ARCHIVED": 409,
        "ACCOUNT_ARCHIVED": 409,
        "ACCOUNT_NOT_FOUND": 404,
        "POSITION_NOT_FOUND": 404,
        "INSTRUMENT_NOT_CONFIRMED": 422,
        "CURRENCY_MISMATCH": 422,
        "VALIDATION_ERROR": 422,
    }
    return api_error(error.code, status_by_code.get(error.code, 503), error.fields)


class PortfolioRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def sanitized(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                fields: dict[str, object] = {
                    ".".join(str(part) for part in item["loc"]): "invalid" for item in error.errors()
                }
                return api_error("VALIDATION_ERROR", 422, fields)

        return sanitized


def build_portfolio_router(service: PortfolioService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["portfolio"], route_class=PortfolioRoute)

    @router.get("/accounts", response_model=CursorPage[AccountView], responses={503: {"model": ErrorEnvelope}})
    async def list_accounts(cursor: UUID | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> object:
        try:
            accounts, next_cursor = await service.list_accounts(None if cursor is None else str(cursor), limit)
            return CursorPage(items=[_account_view(account) for account in accounts], next_cursor=next_cursor)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    @router.post("/accounts", status_code=201, response_model=AccountView, responses={409: {"model": ErrorEnvelope}})
    async def create_account(
        command: AccountCreate, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None
    ) -> object:
        key = _key_or_error(idempotency_key)
        if isinstance(key, JSONResponse):
            return key
        try:
            response = await service.create_account(command, key)
            return JSONResponse(status_code=response.status, content=response.body)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except RepositoryError as error:
            return _repository_error(error)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    @router.patch("/accounts/{account_id}", response_model=AccountView, responses={404: {"model": ErrorEnvelope}})
    async def update_account(
        account_id: UUID,
        command: AccountPatch,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> object:
        key = _key_or_error(idempotency_key)
        if isinstance(key, JSONResponse):
            return key
        try:
            response = await service.update_account(str(account_id), command, key)
            return JSONResponse(status_code=response.status, content=response.body)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except RepositoryError as error:
            return _repository_error(error)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    @router.post(
        "/instruments/confirm",
        status_code=201,
        response_model=InstrumentView,
        responses={422: {"model": ErrorEnvelope}},
    )
    async def confirm_instrument(
        command: InstrumentConfirm,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> object:
        key = _key_or_error(idempotency_key)
        if isinstance(key, JSONResponse):
            return key
        try:
            response = await service.confirm_instrument(command, key)
            return JSONResponse(status_code=response.status, content=response.body)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except RepositoryError as error:
            return _repository_error(error)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    @router.get("/positions", response_model=CursorPage[PositionView])
    async def list_positions(
        archived: bool = False,
        account_id: UUID | None = None,
        cursor: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> object:
        try:
            positions, next_cursor = await service.list_positions(
                archived=archived,
                account_id=None if account_id is None else str(account_id),
                cursor=None if cursor is None else str(cursor),
                limit=limit,
            )
            return CursorPage(items=[_position_view(position) for position in positions], next_cursor=next_cursor)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    @router.get("/portfolio/summary", response_model=PortfolioSummary)
    async def portfolio_summary(currency: Currency) -> object:
        try:
            return await service.summary(currency, datetime.now(UTC))
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    @router.post("/positions", status_code=201, response_model=PositionView)
    async def create_position(
        command: PositionCreate,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> object:
        key = _key_or_error(idempotency_key)
        if isinstance(key, JSONResponse):
            return key
        try:
            response = await service.create_position(command, key)
            return JSONResponse(status_code=response.status, content=response.body)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except RepositoryError as error:
            return _repository_error(error)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    @router.patch("/positions/{position_id}", response_model=PositionView)
    async def update_position(
        position_id: UUID,
        command: PositionPatch,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> object:
        key = _key_or_error(idempotency_key)
        if isinstance(key, JSONResponse):
            return key
        try:
            response = await service.update_position(str(position_id), command, key)
            return JSONResponse(status_code=response.status, content=response.body)
        except DatabaseBusyError:
            return api_error("DATABASE_BUSY", 503)
        except RepositoryError as error:
            return _repository_error(error)
        except DatabaseStartupError:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)
        except Exception:
            return api_error("PORTFOLIO_UNAVAILABLE", 500)

    return router
