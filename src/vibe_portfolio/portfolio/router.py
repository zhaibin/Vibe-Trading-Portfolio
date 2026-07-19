"""Injected FastAPI router for local portfolio resources."""

import re
from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from vibe_portfolio.portfolio.database import DatabaseBusyError, DatabaseStartupError
from vibe_portfolio.portfolio.domain import Currency
from vibe_portfolio.portfolio.repository import RepositoryError
from vibe_portfolio.portfolio.schemas import AccountCreate, AccountPatch, AccountView, CursorPage, ErrorEnvelope
from vibe_portfolio.portfolio.service import PortfolioService
from vibe_portfolio.portfolio.tables import AccountRow

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
        "ACCOUNT_NOT_FOUND": 404,
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

    return router
