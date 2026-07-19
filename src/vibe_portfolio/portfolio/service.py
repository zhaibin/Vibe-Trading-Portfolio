"""Account application rules and transaction boundaries."""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import Currency, DomainValidationError, parse_money
from vibe_portfolio.portfolio.repository import PortfolioRepository, ReplayUnavailable, RepositoryError
from vibe_portfolio.portfolio.schemas import AccountCreate, AccountPatch, AccountView
from vibe_portfolio.portfolio.tables import AccountRow, AccountVersionRow

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


def account_version_response(account: AccountVersionRow) -> dict[str, object]:
    try:
        UUID(account.account_id)
        cash_balance = None if account.cash_balance is None else parse_money(account.cash_balance)
        created_at = _utc_timestamp(account.created_at)
        updated_at = _utc_timestamp(account.updated_at)
        archived_at = None if account.archived_at is None else _utc_timestamp(account.archived_at)
        view = AccountView.model_validate(
            {
                "id": account.account_id,
                "name": account.name,
                "currency": account.currency,
                "cash_balance": cash_balance,
                "version": account.version,
                "created_at": created_at,
                "updated_at": updated_at,
                "archived_at": archived_at,
            }
        )
    except (DomainValidationError, TypeError, ValueError, ValidationError) as error:
        raise ReplayUnavailable() from error
    return view.model_dump(mode="json")


def _utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


class PortfolioService:
    def __init__(self, database: Database, repository: PortfolioRepository | None = None) -> None:
        self.database, self.repository = database, repository or PortfolioRepository()

    async def create_account(self, command: AccountCreate, key: str) -> PortfolioResponse:
        return await self._write("POST:/api/v1/accounts", key, command, None)

    async def update_account(self, account_id: str, command: AccountPatch, key: str) -> PortfolioResponse:
        return await self._write(f"PATCH:/api/v1/accounts/{account_id}", key, command, account_id)

    async def _write(
        self, scope: str, key: str, command: AccountCreate | AccountPatch, account_id: str | None
    ) -> PortfolioResponse:
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            request_hash = canonical_request_hash(command.model_dump(mode="json", exclude_unset=True))
            claim = await self.repository.claim_idempotency(session, scope, key, request_hash, now)
            expected_status = 201 if isinstance(command, AccountCreate) else 200
            if claim.completed:
                resource_id = claim.row.resource_id
                resource_version = claim.row.resource_version
                if (
                    claim.row.response_status != expected_status
                    or not isinstance(resource_id, str)
                    or isinstance(resource_version, bool)
                    or not isinstance(resource_version, int)
                    or resource_version < 1
                    or (account_id is not None and resource_id != account_id)
                ):
                    raise ReplayUnavailable()
                history = await self.repository.account_version(session, resource_id, resource_version)
                if history is None:
                    raise ReplayUnavailable()
                return PortfolioResponse(expected_status, account_version_response(history))
            if isinstance(command, AccountCreate):
                account = await self.repository.create_account(
                    session, command, normalize_account_name(command.name), now
                )
            else:
                assert account_id is not None
                normalized = None if command.name is None else normalize_account_name(command.name)
                account = await self.repository.update_account(session, account_id, command, normalized, now)
            response = account_response(account)
            await self.repository.record_account_version(session, account)
            await self.repository.complete_idempotency(session, claim, account, expected_status)
            return PortfolioResponse(expected_status, response)

    async def list_accounts(self, cursor: str | None, limit: int) -> tuple[list[AccountRow], str | None]:
        async with self.database.session() as session:
            rows = await self.repository.list_accounts(session, cursor, limit)
        page = rows[:limit]
        return page, page[-1].id if len(rows) > limit else None
