"""Account application rules and transaction boundaries."""

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import Currency
from vibe_portfolio.portfolio.repository import PortfolioRepository, RepositoryError
from vibe_portfolio.portfolio.schemas import AccountCreate, AccountPatch, AccountView
from vibe_portfolio.portfolio.tables import AccountRow

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class PortfolioResponse:
    status: int
    body: dict[str, object]


def normalize_account_name(value: str) -> str:
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()
    if not 1 <= len(normalized) <= 80:
        raise RepositoryError("VALIDATION_ERROR", fields={"name": "invalid_length"})
    return normalized


def canonical_request_hash(payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(serialized.encode()).hexdigest()


def account_response(account: AccountRow) -> dict[str, object]:
    return AccountView(
        id=account.id,
        name=account.name,
        currency=Currency(account.currency),
        cash_balance=account.cash_balance,
        version=account.version,
        created_at=account.created_at,
        updated_at=account.updated_at,
        archived_at=account.archived_at,
    ).model_dump(mode="json")


class PortfolioService:
    def __init__(self, database: Database, repository: PortfolioRepository | None = None) -> None:
        self.database, self.repository = database, repository or PortfolioRepository()
        self._claim_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def create_account(self, command: AccountCreate, key: str) -> PortfolioResponse:
        return await self._write("POST:/api/v1/accounts", key, command, None)

    async def update_account(self, account_id: str, command: AccountPatch, key: str) -> PortfolioResponse:
        return await self._write(f"PATCH:/api/v1/accounts/{account_id}", key, command, account_id)

    async def _write(
        self, scope: str, key: str, command: AccountCreate | AccountPatch, account_id: str | None
    ) -> PortfolioResponse:
        async with self._claim_locks.setdefault((scope, key), asyncio.Lock()):
            now = datetime.now(UTC)
            async with self.database.session() as session, session.begin():
                request_hash = canonical_request_hash(command.model_dump(mode="json", exclude_unset=True))
                claim = await self.repository.claim_idempotency(session, scope, key, request_hash, now)
                if claim.completed:
                    if claim.row.response_json is None or claim.row.response_status is None:
                        raise RepositoryError("PORTFOLIO_UNAVAILABLE")
                    value = json.loads(claim.row.response_json)
                    if not isinstance(value, dict):
                        raise RepositoryError("PORTFOLIO_UNAVAILABLE")
                    return PortfolioResponse(claim.row.response_status, value)
                if isinstance(command, AccountCreate):
                    account = await self.repository.create_account(
                        session, command, normalize_account_name(command.name), now
                    )
                    status = 201
                else:
                    assert account_id is not None
                    normalized = None if command.name is None else normalize_account_name(command.name)
                    account = await self.repository.update_account(session, account_id, command, normalized, now)
                    status = 200
                response = account_response(account)
                await self.repository.complete_idempotency(session, claim, account, status, response)
                return PortfolioResponse(status, response)

    async def list_accounts(self, cursor: str | None, limit: int) -> tuple[list[AccountRow], str | None]:
        async with self.database.session() as session:
            rows = await self.repository.list_accounts(session, cursor, limit)
        page = rows[:limit]
        return page, page[-1].id if len(rows) > limit else None
