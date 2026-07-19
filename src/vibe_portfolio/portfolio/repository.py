"""Transactional persistence operations for portfolio resources."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Final
from uuid import uuid4

from sqlalchemy import Select, exists, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from vibe_portfolio.portfolio.schemas import AccountCreate, AccountPatch
from vibe_portfolio.portfolio.tables import AccountRow, AccountVersionRow, IdempotencyRow, PositionRow

IDEMPOTENCY_TTL: Final = timedelta(hours=24)


class RepositoryError(RuntimeError):
    code: str

    def __init__(self, code: str, *, fields: dict[str, object] | None = None) -> None:
        self.code = code
        self.fields = fields
        super().__init__(code)


class IdempotencyConflict(RepositoryError):
    def __init__(self) -> None:
        super().__init__("IDEMPOTENCY_CONFLICT")


class ReplayUnavailable(RepositoryError):
    def __init__(self) -> None:
        super().__init__("PORTFOLIO_UNAVAILABLE")


class DuplicateAccountName(RepositoryError):
    def __init__(self) -> None:
        super().__init__("DUPLICATE_ACCOUNT_NAME")


class AccountNotFound(RepositoryError):
    def __init__(self) -> None:
        super().__init__("ACCOUNT_NOT_FOUND")


class ConcurrentModification(RepositoryError):
    def __init__(self, version: int | None = None) -> None:
        super().__init__("CONCURRENT_MODIFICATION", fields=None if version is None else {"version": version})


class AccountHasActivePositions(RepositoryError):
    def __init__(self) -> None:
        super().__init__("ACCOUNT_HAS_ACTIVE_POSITIONS")


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    row: IdempotencyRow
    completed: bool


def hash_idempotency_key(key: str) -> str:
    return sha256(key.encode("ascii")).hexdigest()


class PortfolioRepository:
    """Use an injected transaction; this class never owns session lifecycle."""

    async def claim_idempotency(
        self, session: AsyncSession, scope: str, key: str, request_hash: str, now: datetime
    ) -> IdempotencyClaim:
        key_hash = hash_idempotency_key(key)
        pending_values = {
            "scope": scope,
            "key_hash": key_hash,
            "request_hash": request_hash,
            "state": "pending",
            "resource_id": None,
            "resource_version": None,
            "response_status": None,
            "created_at": now,
            "expires_at": now + IDEMPOTENCY_TTL,
        }
        inserted = await session.scalar(
            sqlite_insert(IdempotencyRow)
            .values(**pending_values)
            .on_conflict_do_nothing(index_elements=[IdempotencyRow.scope, IdempotencyRow.key_hash])
            .returning(IdempotencyRow.scope)
        )
        if inserted is None:
            reclaimed = await session.scalar(
                update(IdempotencyRow)
                .where(
                    IdempotencyRow.scope == scope,
                    IdempotencyRow.key_hash == key_hash,
                    IdempotencyRow.expires_at <= now,
                )
                .values(**pending_values)
                .returning(IdempotencyRow.scope)
            )
            if reclaimed is None:
                row = await session.get(IdempotencyRow, (scope, key_hash), populate_existing=True)
                if row is None:
                    raise ReplayUnavailable()
                if row.request_hash != request_hash:
                    raise IdempotencyConflict()
                if row.state != "completed":
                    raise ReplayUnavailable()
                return IdempotencyClaim(row=row, completed=True)
        row = await session.get(IdempotencyRow, (scope, key_hash), populate_existing=True)
        if row is None or row.state != "pending" or row.request_hash != request_hash:
            raise ReplayUnavailable()
        return IdempotencyClaim(row=row, completed=False)

    async def complete_idempotency(
        self,
        session: AsyncSession,
        claim: IdempotencyClaim,
        account: AccountRow,
        status: int,
    ) -> None:
        completed = await session.scalar(
            update(IdempotencyRow)
            .where(
                IdempotencyRow.scope == claim.row.scope,
                IdempotencyRow.key_hash == claim.row.key_hash,
                IdempotencyRow.request_hash == claim.row.request_hash,
                IdempotencyRow.state == "pending",
            )
            .values(
                state="completed",
                resource_id=account.id,
                resource_version=account.version,
                response_status=status,
            )
            .returning(IdempotencyRow.scope)
        )
        if completed is None:
            raise ReplayUnavailable()

    async def record_account_version(self, session: AsyncSession, account: AccountRow) -> None:
        recorded = await session.scalar(
            sqlite_insert(AccountVersionRow)
            .values(
                account_id=account.id,
                version=account.version,
                name=account.name,
                currency=account.currency,
                cash_balance=None if account.cash_balance is None else format(account.cash_balance, "f"),
                created_at=account.created_at.isoformat(),
                updated_at=account.updated_at.isoformat(),
                archived_at=None if account.archived_at is None else account.archived_at.isoformat(),
            )
            .on_conflict_do_nothing(index_elements=[AccountVersionRow.account_id, AccountVersionRow.version])
            .returning(AccountVersionRow.account_id)
        )
        if recorded is None:
            raise ReplayUnavailable()

    async def account_version(
        self, session: AsyncSession, account_id: str, version: int
    ) -> AccountVersionRow | None:
        return await session.get(AccountVersionRow, (account_id, version))

    async def account(self, session: AsyncSession, account_id: str) -> AccountRow | None:
        return await session.get(AccountRow, account_id)

    async def create_account(
        self, session: AsyncSession, command: AccountCreate, normalized_name: str, now: datetime
    ) -> AccountRow:
        account = await session.scalar(
            sqlite_insert(AccountRow)
            .values(
                id=str(uuid4()),
                name=command.name,
                normalized_name=normalized_name,
                currency=command.currency.value,
                cash_balance=command.cash_balance,
                version=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=[AccountRow.normalized_name],
                index_where=AccountRow.archived_at.is_(None),
            )
            .returning(AccountRow)
        )
        if account is None:
            raise DuplicateAccountName()
        return account

    async def update_account(
        self,
        session: AsyncSession,
        account_id: str,
        command: AccountPatch,
        normalized_name: str | None,
        now: datetime,
    ) -> AccountRow:
        current = await self.account(session, account_id)
        if current is None:
            raise AccountNotFound()
        if current.version != command.version:
            raise ConcurrentModification(current.version)
        final_normalized_name = current.normalized_name if normalized_name is None else normalized_name
        if normalized_name is not None and normalized_name != current.normalized_name:
            duplicate = await session.scalar(
                select(
                    exists().where(
                        AccountRow.id != account_id,
                        AccountRow.normalized_name == normalized_name,
                        AccountRow.archived_at.is_(None),
                    )
                )
            )
            if duplicate:
                raise DuplicateAccountName()
        archived_at = current.archived_at
        if command.archived is True and current.archived_at is None:
            has_positions = await session.scalar(
                select(exists().where(PositionRow.account_id == account_id, PositionRow.archived_at.is_(None)))
            )
            if has_positions:
                raise AccountHasActivePositions()
            archived_at = now
        elif command.archived is False:
            duplicate = await session.scalar(
                select(
                    exists().where(
                        AccountRow.id != account_id,
                        AccountRow.normalized_name == final_normalized_name,
                        AccountRow.archived_at.is_(None),
                    )
                )
            )
            if duplicate:
                raise DuplicateAccountName()
            archived_at = None
        values: dict[str, object] = {"updated_at": now, "version": command.version + 1, "archived_at": archived_at}
        if command.name is not None:
            values["name"] = command.name
            assert normalized_name is not None
            values["normalized_name"] = normalized_name
        if "cash_balance" in command.model_fields_set:
            values["cash_balance"] = command.cash_balance
        result = await session.execute(
            update(AccountRow)
            .prefix_with("OR IGNORE", dialect="sqlite")
            .where(AccountRow.id == account_id, AccountRow.version == command.version)
            .values(**values)
            .returning(AccountRow.id)
        )
        if result.scalar_one_or_none() is None:
            if archived_at is None:
                duplicate = await session.scalar(
                    select(
                        exists().where(
                            AccountRow.id != account_id,
                            AccountRow.normalized_name == final_normalized_name,
                            AccountRow.archived_at.is_(None),
                        )
                    )
                )
                if duplicate:
                    raise DuplicateAccountName()
            raise ConcurrentModification(current.version)
        updated = await session.get(AccountRow, account_id, populate_existing=True)
        assert updated is not None
        return updated

    async def list_accounts(self, session: AsyncSession, cursor: str | None, limit: int) -> list[AccountRow]:
        statement: Select[tuple[AccountRow]] = (
            select(AccountRow).where(AccountRow.archived_at.is_(None)).order_by(AccountRow.id)
        )
        if cursor is not None:
            statement = statement.where(AccountRow.id > cursor)
        return list((await session.scalars(statement.limit(limit + 1))).all())
