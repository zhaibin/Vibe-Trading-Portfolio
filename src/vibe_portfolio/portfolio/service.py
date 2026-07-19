"""Account application rules and transaction boundaries."""

import json
import re
import unicodedata
from datetime import UTC, datetime
from hashlib import sha256

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.repository import PortfolioRepository, RepositoryError
from vibe_portfolio.portfolio.schemas import AccountCreate, AccountPatch
from vibe_portfolio.portfolio.tables import AccountRow

_WHITESPACE = re.compile(r"\s+")


def normalize_account_name(value: str) -> str:
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()
    if not 1 <= len(normalized) <= 80:
        raise RepositoryError("VALIDATION_ERROR", fields={"name": "invalid_length"})
    return normalized


def canonical_request_hash(payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(serialized.encode("utf-8")).hexdigest()


class PortfolioService:
    def __init__(self, database: Database, repository: PortfolioRepository | None = None) -> None:
        self.database = database
        self.repository = repository or PortfolioRepository()

    async def create_account(self, command: AccountCreate, key: str) -> AccountRow:
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            claim = await self.repository.claim_idempotency(
                session, "POST:/api/v1/accounts", key, canonical_request_hash(command.model_dump(mode="json")), now
            )
            if claim.completed:
                assert claim.row.resource_id is not None
                replay = await self.repository.account(session, claim.row.resource_id)
                if replay is None:
                    raise RepositoryError("PORTFOLIO_UNAVAILABLE")
                return replay
            account = await self.repository.create_account(session, command, normalize_account_name(command.name), now)
            await self.repository.complete_idempotency(session, claim, account, 201)
            return account

    async def update_account(self, account_id: str, command: AccountPatch, key: str) -> AccountRow:
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            claim = await self.repository.claim_idempotency(
                session,
                f"PATCH:/api/v1/accounts/{account_id}",
                key,
                canonical_request_hash(command.model_dump(mode="json", exclude_unset=True)),
                now,
            )
            if claim.completed:
                assert claim.row.resource_id is not None
                replay = await self.repository.account(session, claim.row.resource_id)
                if replay is None:
                    raise RepositoryError("PORTFOLIO_UNAVAILABLE")
                return replay
            normalized_name = None if command.name is None else normalize_account_name(command.name)
            account = await self.repository.update_account(session, account_id, command, normalized_name, now)
            await self.repository.complete_idempotency(session, claim, account, 200)
            return account

    async def list_accounts(self, cursor: str | None, limit: int) -> tuple[list[AccountRow], str | None]:
        async with self.database.session() as session:
            rows = await self.repository.list_accounts(session, cursor, limit)
        page = rows[:limit]
        return page, page[-1].id if len(rows) > limit else None
