"""Transactional persistence operations for portfolio resources."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Final
from uuid import uuid4

from sqlalchemy import Select, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vibe_portfolio.portfolio.schemas import AccountCreate, AccountPatch
from vibe_portfolio.portfolio.tables import AccountRow, IdempotencyRow, PositionRow

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
        row = await session.get(IdempotencyRow, (scope, key_hash))
        if row is not None:
            if row.expires_at <= now:
                row.request_hash, row.state = request_hash, "pending"
                row.resource_id = row.response_status = row.response_json = None
                row.created_at, row.expires_at = now, now + IDEMPOTENCY_TTL
                await session.flush()
                return IdempotencyClaim(row=row, completed=False)
            if row.request_hash != request_hash:
                raise IdempotencyConflict()
            else:
                return IdempotencyClaim(row=row, completed=row.state == "completed")
        row = IdempotencyRow(
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            state="pending",
            resource_id=None,
            response_status=None,
            response_json=None,
            created_at=now,
            expires_at=now + IDEMPOTENCY_TTL,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError as error:
            row = await session.get(IdempotencyRow, (scope, key_hash))
            if row is None:
                raise
            if row.expires_at <= now:
                row.request_hash, row.state = request_hash, "pending"
                row.resource_id = row.response_status = row.response_json = None
                row.created_at, row.expires_at = now, now + IDEMPOTENCY_TTL
                await session.flush()
                return IdempotencyClaim(row=row, completed=False)
            if row.request_hash != request_hash:
                raise IdempotencyConflict() from error
            return IdempotencyClaim(row=row, completed=row.state == "completed")
        return IdempotencyClaim(row=row, completed=False)

    async def complete_idempotency(
        self,
        session: AsyncSession,
        claim: IdempotencyClaim,
        account: AccountRow,
        status: int,
        response: dict[str, object],
    ) -> None:
        claim.row.state = "completed"
        claim.row.resource_id = account.id
        claim.row.response_status = status
        claim.row.response_json = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        await session.flush()

    async def account(self, session: AsyncSession, account_id: str) -> AccountRow | None:
        return await session.get(AccountRow, account_id)

    async def create_account(
        self, session: AsyncSession, command: AccountCreate, normalized_name: str, now: datetime
    ) -> AccountRow:
        duplicate = await session.scalar(
            select(exists().where(AccountRow.normalized_name == normalized_name, AccountRow.archived_at.is_(None)))
        )
        if duplicate:
            raise DuplicateAccountName()
        account = AccountRow(
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
        session.add(account)
        await session.flush()
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
                        AccountRow.normalized_name == current.normalized_name,
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
        try:
            async with session.begin_nested():
                result = await session.execute(
                    update(AccountRow)
                    .where(AccountRow.id == account_id, AccountRow.version == command.version)
                    .values(**values)
                    .returning(AccountRow.id)
                )
        except IntegrityError as error:
            if "normalized_name" in str(error):
                raise DuplicateAccountName() from error
            raise
        if result.scalar_one_or_none() is None:
            latest = await self.account(session, account_id)
            raise ConcurrentModification(None if latest is None else latest.version)
        await session.flush()
        updated = await self.account(session, account_id)
        assert updated is not None
        return updated

    async def list_accounts(self, session: AsyncSession, cursor: str | None, limit: int) -> list[AccountRow]:
        statement: Select[tuple[AccountRow]] = (
            select(AccountRow).where(AccountRow.archived_at.is_(None)).order_by(AccountRow.id)
        )
        if cursor is not None:
            statement = statement.where(AccountRow.id > cursor)
        return list((await session.scalars(statement.limit(limit + 1))).all())
