"""Transactional persistence operations for portfolio resources."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Final, Protocol
from uuid import uuid4

from sqlalchemy import Select, delete, exists, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from vibe_portfolio.portfolio.schemas import AccountCreate, AccountPatch, PositionCreate, PositionPatch
from vibe_portfolio.portfolio.tables import (
    AccountRow,
    AccountVersionRow,
    IdempotencyRow,
    InstrumentCandidateRow,
    InstrumentProviderSymbolRow,
    InstrumentRow,
    LatestQuoteRow,
    PositionRow,
    PositionVersionRow,
    QuoteRefreshItemRow,
    QuoteRefreshRunRow,
)

IDEMPOTENCY_TTL: Final = timedelta(hours=24)
CANDIDATE_TTL: Final = timedelta(minutes=15)
REFRESH_DETAIL_TTL: Final = timedelta(days=90)
REFRESH_RUN_TTL: Final = timedelta(days=365)
PRUNE_LIMIT: Final = 1_000


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


class InstrumentNotConfirmed(RepositoryError):
    def __init__(self) -> None:
        super().__init__("INSTRUMENT_NOT_CONFIRMED")


class CurrencyMismatch(RepositoryError):
    def __init__(self) -> None:
        super().__init__("CURRENCY_MISMATCH")


class DuplicatePosition(RepositoryError):
    def __init__(self) -> None:
        super().__init__("DUPLICATE_POSITION")


class PositionNotFound(RepositoryError):
    def __init__(self) -> None:
        super().__init__("POSITION_NOT_FOUND")


class PositionArchived(RepositoryError):
    def __init__(self) -> None:
        super().__init__("POSITION_ARCHIVED")


class AccountArchived(RepositoryError):
    def __init__(self) -> None:
        super().__init__("ACCOUNT_ARCHIVED")


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    row: IdempotencyRow
    completed: bool


@dataclass(frozen=True, slots=True)
class SummaryRecords:
    accounts: list[AccountRow]
    positions: list[PositionRow]
    quotes: dict[str, LatestQuoteRow]
    latest_attempts: dict[str, QuoteRefreshItemRow]


@dataclass(frozen=True, slots=True)
class RefreshInstrumentRecord:
    instrument: InstrumentRow
    mappings: dict[str, str]
    has_quote: bool


@dataclass(frozen=True, slots=True)
class RefreshItemInput:
    instrument_id: str
    outcome: str
    provider: str | None
    error_code: str | None
    price: Decimal | None = None
    currency: str | None = None
    provider_symbol: str | None = None
    as_of: datetime | None = None


def hash_idempotency_key(key: str) -> str:
    return sha256(key.encode("ascii")).hexdigest()


class ProviderSymbolInput(Protocol):
    provider: str
    symbol: str


class CandidateInput(Protocol):
    canonical_symbol: str
    name: str
    market: object
    currency: object
    asset_type: object
    provider_symbols: Sequence[ProviderSymbolInput]


def _valid_candidate_lifetime(candidate: InstrumentCandidateRow, now: datetime) -> bool:
    try:
        return (
            candidate.created_at <= now < candidate.expires_at
            and candidate.expires_at <= candidate.created_at + CANDIDATE_TTL
        )
    except TypeError:
        return False


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
        await self.complete_resource_idempotency(
            session,
            claim,
            resource_id=account.id,
            resource_version=account.version,
            status=status,
        )

    async def complete_resource_idempotency(
        self,
        session: AsyncSession,
        claim: IdempotencyClaim,
        *,
        resource_id: str,
        resource_version: int,
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
                resource_id=resource_id,
                resource_version=resource_version,
                response_status=status,
            )
            .returning(IdempotencyRow.scope)
        )
        if completed is None:
            raise ReplayUnavailable()

    async def completed_idempotency(
        self, session: AsyncSession, scope: str, key: str, request_hash: str, now: datetime
    ) -> IdempotencyRow | None:
        row = await session.get(IdempotencyRow, (scope, hash_idempotency_key(key)))
        if row is None or row.expires_at <= now:
            return None
        if row.request_hash != request_hash:
            raise IdempotencyConflict()
        return row if row.state == "completed" else None

    async def active_refresh_instruments(
        self, session: AsyncSession, instrument_ids: Sequence[str] | None
    ) -> list[RefreshInstrumentRecord]:
        statement = (
            select(InstrumentRow)
            .join(PositionRow, PositionRow.instrument_id == InstrumentRow.id)
            .join(AccountRow, AccountRow.id == PositionRow.account_id)
            .where(PositionRow.archived_at.is_(None), AccountRow.archived_at.is_(None))
            .distinct()
            .order_by(InstrumentRow.id)
        )
        if instrument_ids is not None:
            statement = statement.where(InstrumentRow.id.in_(instrument_ids))
        instruments = list((await session.scalars(statement)).all())
        ids = [instrument.id for instrument in instruments]
        if not ids:
            return []
        mapping_rows = (
            await session.scalars(
                select(InstrumentProviderSymbolRow).where(InstrumentProviderSymbolRow.instrument_id.in_(ids))
            )
        ).all()
        mappings: dict[str, dict[str, str]] = {instrument_id: {} for instrument_id in ids}
        for mapping in mapping_rows:
            mappings[mapping.instrument_id][mapping.provider] = mapping.provider_symbol
        quoted = set(
            await session.scalars(select(LatestQuoteRow.instrument_id).where(LatestQuoteRow.instrument_id.in_(ids)))
        )
        return [
            RefreshInstrumentRecord(
                instrument=instrument,
                mappings=mappings[instrument.id],
                has_quote=instrument.id in quoted,
            )
            for instrument in instruments
        ]

    async def running_refresh(self, session: AsyncSession) -> QuoteRefreshRunRow | None:
        run: QuoteRefreshRunRow | None = await session.scalar(
            select(QuoteRefreshRunRow)
            .where(QuoteRefreshRunRow.status == "running")
            .order_by(QuoteRefreshRunRow.started_at.desc())
            .limit(1)
        )
        return run

    async def refresh_run(self, session: AsyncSession, run_id: str) -> QuoteRefreshRunRow | None:
        return await session.get(QuoteRefreshRunRow, run_id)

    async def refresh_items(self, session: AsyncSession, run_id: str) -> list[QuoteRefreshItemRow]:
        return list(
            (
                await session.scalars(
                    select(QuoteRefreshItemRow)
                    .where(QuoteRefreshItemRow.run_id == run_id)
                    .order_by(QuoteRefreshItemRow.instrument_id)
                )
            ).all()
        )

    async def abandon_running_refreshes(self, session: AsyncSession, now: datetime) -> None:
        run_ids = list(
            await session.scalars(select(QuoteRefreshRunRow.id).where(QuoteRefreshRunRow.status == "running"))
        )
        if not run_ids:
            return
        await session.execute(
            update(QuoteRefreshRunRow)
            .where(QuoteRefreshRunRow.id.in_(run_ids), QuoteRefreshRunRow.status == "running")
            .values(status="failed", finished_at=now)
        )
        await session.execute(
            update(QuoteRefreshItemRow)
            .where(QuoteRefreshItemRow.run_id.in_(run_ids))
            .values(error_code="REFRESH_ABANDONED")
        )

    async def complete_refresh(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        status: str,
        items: Sequence[RefreshItemInput],
        finished_at: datetime,
        claim: IdempotencyClaim,
    ) -> None:
        counts = {"updated": 0, "stale": 0, "unavailable": 0}
        for item in items:
            counts[item.outcome] += 1
            session.add(
                QuoteRefreshItemRow(
                    run_id=run_id,
                    instrument_id=item.instrument_id,
                    outcome=item.outcome,
                    provider=item.provider,
                    error_code=item.error_code,
                    created_at=finished_at,
                )
            )
            if item.outcome == "updated":
                assert None not in (item.price, item.currency, item.provider, item.provider_symbol, item.as_of)
                await session.execute(
                    sqlite_insert(LatestQuoteRow)
                    .values(
                        instrument_id=item.instrument_id,
                        price=item.price,
                        currency=item.currency,
                        provider=item.provider,
                        provider_symbol=item.provider_symbol,
                        as_of=item.as_of,
                        fetched_at=finished_at,
                        refresh_run_id=run_id,
                    )
                    .on_conflict_do_update(
                        index_elements=[LatestQuoteRow.instrument_id],
                        set_={
                            "price": item.price,
                            "currency": item.currency,
                            "provider": item.provider,
                            "provider_symbol": item.provider_symbol,
                            "as_of": item.as_of,
                            "fetched_at": finished_at,
                            "refresh_run_id": run_id,
                        },
                    )
                )
        completed = await session.scalar(
            update(QuoteRefreshRunRow)
            .where(QuoteRefreshRunRow.id == run_id, QuoteRefreshRunRow.status == "running")
            .values(
                status=status,
                finished_at=finished_at,
                updated_count=counts["updated"],
                stale_count=counts["stale"],
                unavailable_count=counts["unavailable"],
            )
            .returning(QuoteRefreshRunRow.id)
        )
        if completed is None:
            raise ReplayUnavailable()
        await self.complete_resource_idempotency(
            session,
            claim,
            resource_id=run_id,
            resource_version=1,
            status=502 if status == "failed" else 200,
        )

    async def prune_expired(self, session: AsyncSession, now: datetime, limit: int = PRUNE_LIMIT) -> int:
        remaining = min(max(limit, 0), PRUNE_LIMIT)
        deleted = 0
        statements = (
            (InstrumentCandidateRow, InstrumentCandidateRow.id, InstrumentCandidateRow.expires_at <= now),
            (IdempotencyRow, (IdempotencyRow.scope, IdempotencyRow.key_hash), IdempotencyRow.expires_at <= now),
            (
                QuoteRefreshItemRow,
                (QuoteRefreshItemRow.run_id, QuoteRefreshItemRow.instrument_id),
                QuoteRefreshItemRow.created_at <= now - REFRESH_DETAIL_TTL,
            ),
        )
        for model, key, predicate in statements:
            if remaining == 0:
                break
            if isinstance(key, tuple):
                rows = list((await session.execute(select(*key).where(predicate).limit(remaining))).all())
                for values in rows:
                    await session.execute(
                        delete(model).where(*(column == value for column, value in zip(key, values, strict=True)))
                    )
            else:
                rows = list(await session.scalars(select(key).where(predicate).limit(remaining)))
                if rows:
                    await session.execute(delete(model).where(key.in_(rows)))
            deleted += len(rows)
            remaining -= len(rows)
        if remaining:
            run_ids = list(
                await session.scalars(
                    select(QuoteRefreshRunRow.id)
                    .where(
                        QuoteRefreshRunRow.finished_at <= now - REFRESH_RUN_TTL,
                        ~exists().where(LatestQuoteRow.refresh_run_id == QuoteRefreshRunRow.id),
                    )
                    .limit(remaining)
                )
            )
            if run_ids:
                await session.execute(delete(QuoteRefreshRunRow).where(QuoteRefreshRunRow.id.in_(run_ids)))
            deleted += len(run_ids)
        return deleted

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

    async def cache_candidates(
        self,
        session: AsyncSession,
        candidates: Sequence[CandidateInput],
        *,
        now: datetime,
    ) -> list[InstrumentCandidateRow]:
        rows: list[InstrumentCandidateRow] = []
        for candidate in candidates:
            mappings = [
                {"provider": mapping.provider, "symbol": mapping.symbol}
                for mapping in candidate.provider_symbols
            ]
            if not mappings:
                raise InstrumentNotConfirmed()
            row = InstrumentCandidateRow(
                id=str(uuid4()),
                canonical_symbol=candidate.canonical_symbol,
                name=candidate.name,
                market=str(getattr(candidate.market, "value", candidate.market)),
                currency=str(getattr(candidate.currency, "value", candidate.currency)),
                asset_type=str(getattr(candidate.asset_type, "value", candidate.asset_type)),
                provider=mappings[0]["provider"],
                provider_symbols_json=json.dumps(mappings, sort_keys=True, separators=(",", ":")),
                created_at=now,
                expires_at=now + CANDIDATE_TTL,
                consumed_at=None,
            )
            session.add(row)
            rows.append(row)
        await session.flush()
        return rows

    async def candidate(
        self, session: AsyncSession, candidate_id: str, now: datetime
    ) -> InstrumentCandidateRow | None:
        statement: Select[tuple[InstrumentCandidateRow]] = select(InstrumentCandidateRow).where(
            InstrumentCandidateRow.id == candidate_id,
            InstrumentCandidateRow.consumed_at.is_(None),
        )
        result = await session.execute(statement)
        candidate = result.scalar_one_or_none()
        if candidate is None or not _valid_candidate_lifetime(candidate, now):
            return None
        return candidate

    async def consume_candidate(
        self, session: AsyncSession, candidate_id: str, now: datetime
    ) -> None:
        consumed = await session.scalar(
            update(InstrumentCandidateRow)
            .where(
                InstrumentCandidateRow.id == candidate_id,
                InstrumentCandidateRow.consumed_at.is_(None),
                InstrumentCandidateRow.created_at <= now,
                InstrumentCandidateRow.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(InstrumentCandidateRow.id)
        )
        if consumed is None:
            raise InstrumentNotConfirmed()

    async def instrument(self, session: AsyncSession, instrument_id: str) -> InstrumentRow | None:
        return await session.get(InstrumentRow, instrument_id)

    async def upsert_instrument(
        self,
        session: AsyncSession,
        candidate: InstrumentCandidateRow,
        provider_symbols: Sequence[tuple[str, str]],
        now: datetime,
    ) -> InstrumentRow:
        await session.scalar(
            sqlite_insert(InstrumentRow)
            .values(
                id=str(uuid4()),
                canonical_symbol=candidate.canonical_symbol,
                name=candidate.name,
                market=candidate.market,
                currency=candidate.currency,
                asset_type=candidate.asset_type,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[InstrumentRow.canonical_symbol])
            .returning(InstrumentRow.id)
        )
        instrument = await session.scalar(
            select(InstrumentRow).where(InstrumentRow.canonical_symbol == candidate.canonical_symbol)
        )
        if instrument is None or (
            instrument.market,
            instrument.currency,
            instrument.asset_type,
        ) != (candidate.market, candidate.currency, candidate.asset_type):
            raise InstrumentNotConfirmed()
        for provider, provider_symbol in provider_symbols:
            await session.execute(
                sqlite_insert(InstrumentProviderSymbolRow)
                .values(
                    instrument_id=instrument.id,
                    provider=provider,
                    provider_symbol=provider_symbol,
                )
                .on_conflict_do_nothing()
            )
            stored = await session.get(InstrumentProviderSymbolRow, (instrument.id, provider))
            if stored is None or stored.provider_symbol != provider_symbol:
                raise InstrumentNotConfirmed()
        return instrument

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

    async def record_position_version(self, session: AsyncSession, position: PositionRow) -> None:
        recorded = await session.scalar(
            sqlite_insert(PositionVersionRow)
            .values(
                position_id=position.id,
                version=position.version,
                account_id=position.account_id,
                instrument_id=position.instrument_id,
                quantity=format(position.quantity, "f"),
                average_cost=format(position.average_cost, "f"),
                note=position.note,
                created_at=position.created_at.isoformat(),
                updated_at=position.updated_at.isoformat(),
                archived_at=None if position.archived_at is None else position.archived_at.isoformat(),
            )
            .on_conflict_do_nothing(
                index_elements=[PositionVersionRow.position_id, PositionVersionRow.version]
            )
            .returning(PositionVersionRow.position_id)
        )
        if recorded is None:
            raise ReplayUnavailable()

    async def position_version(
        self, session: AsyncSession, position_id: str, version: int
    ) -> PositionVersionRow | None:
        return await session.get(PositionVersionRow, (position_id, version))

    async def position(self, session: AsyncSession, position_id: str) -> PositionRow | None:
        return await session.get(PositionRow, position_id)

    async def create_position(
        self,
        session: AsyncSession,
        command: PositionCreate,
        note: str | None,
        now: datetime,
    ) -> PositionRow:
        account_id = str(command.account_id)
        instrument_id = str(command.instrument_id)
        account = await self.account(session, account_id)
        if account is None:
            raise AccountNotFound()
        if account.archived_at is not None:
            raise AccountArchived()
        instrument = await self.instrument(session, instrument_id)
        if instrument is None:
            raise InstrumentNotConfirmed()
        if instrument.currency != account.currency:
            raise CurrencyMismatch()
        position = await session.scalar(
            sqlite_insert(PositionRow)
            .values(
                id=str(uuid4()),
                account_id=account_id,
                instrument_id=instrument_id,
                quantity=command.quantity,
                average_cost=command.average_cost,
                note=note,
                version=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=[PositionRow.account_id, PositionRow.instrument_id],
                index_where=PositionRow.archived_at.is_(None),
            )
            .returning(PositionRow)
        )
        if position is None:
            raise DuplicatePosition()
        return position

    async def update_position(
        self,
        session: AsyncSession,
        position_id: str,
        command: PositionPatch,
        note: str | None,
        now: datetime,
    ) -> PositionRow:
        current = await self.position(session, position_id)
        if current is None:
            raise PositionNotFound()
        if current.version != command.version:
            raise ConcurrentModification(current.version)
        if current.archived_at is not None and command.archived is not False:
            raise PositionArchived()
        archived_at = current.archived_at
        if command.archived is True and archived_at is None:
            archived_at = now
        elif command.archived is False:
            archived_at = None
        if archived_at is None:
            account = await self.account(session, current.account_id)
            if account is None:
                raise AccountNotFound()
            if account.archived_at is not None:
                raise AccountArchived()
            instrument = await self.instrument(session, current.instrument_id)
            if instrument is None:
                raise InstrumentNotConfirmed()
            if account.currency != instrument.currency:
                raise CurrencyMismatch()
        values: dict[str, object] = {
            "version": command.version + 1,
            "updated_at": now,
            "archived_at": archived_at,
        }
        if "quantity" in command.model_fields_set:
            assert command.quantity is not None
            values["quantity"] = command.quantity
        if "average_cost" in command.model_fields_set:
            assert command.average_cost is not None
            values["average_cost"] = command.average_cost
        if "note" in command.model_fields_set:
            values["note"] = note
        result = await session.execute(
            update(PositionRow)
            .prefix_with("OR IGNORE", dialect="sqlite")
            .where(PositionRow.id == position_id, PositionRow.version == command.version)
            .values(**values)
            .returning(PositionRow.id)
        )
        if result.scalar_one_or_none() is None:
            duplicate = await session.scalar(
                select(
                    exists().where(
                        PositionRow.id != position_id,
                        PositionRow.account_id == current.account_id,
                        PositionRow.instrument_id == current.instrument_id,
                        PositionRow.archived_at.is_(None),
                    )
                )
            )
            if archived_at is None and duplicate:
                raise DuplicatePosition()
            raise ConcurrentModification(current.version)
        updated_position = await session.get(PositionRow, position_id, populate_existing=True)
        assert updated_position is not None
        return updated_position

    async def list_positions(
        self,
        session: AsyncSession,
        *,
        archived: bool,
        account_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[PositionRow]:
        archived_filter = PositionRow.archived_at.is_not(None) if archived else PositionRow.archived_at.is_(None)
        statement: Select[tuple[PositionRow]] = (
            select(PositionRow).where(archived_filter).order_by(PositionRow.id)
        )
        if account_id is not None:
            statement = statement.where(PositionRow.account_id == account_id)
        if cursor is not None:
            statement = statement.where(PositionRow.id > cursor)
        return list((await session.scalars(statement.limit(limit + 1))).all())

    async def summary_records(self, session: AsyncSession, currency: str) -> SummaryRecords:
        accounts = list(
            (
                await session.scalars(
                    select(AccountRow).where(AccountRow.currency == currency, AccountRow.archived_at.is_(None))
                )
            ).all()
        )
        account_ids = [account.id for account in accounts]
        if not account_ids:
            return SummaryRecords(accounts=accounts, positions=[], quotes={}, latest_attempts={})

        positions = list(
            (
                await session.scalars(
                    select(PositionRow).where(
                        PositionRow.account_id.in_(account_ids),
                        PositionRow.archived_at.is_(None),
                    ).order_by(PositionRow.id)
                )
            ).all()
        )
        instrument_ids = {position.instrument_id for position in positions}
        if not instrument_ids:
            return SummaryRecords(accounts=accounts, positions=positions, quotes={}, latest_attempts={})

        quotes = {
            quote.instrument_id: quote
            for quote in (
                await session.scalars(select(LatestQuoteRow).where(LatestQuoteRow.instrument_id.in_(instrument_ids)))
            ).all()
        }
        attempts = (
            await session.scalars(
                select(QuoteRefreshItemRow)
                .join(QuoteRefreshRunRow, QuoteRefreshRunRow.id == QuoteRefreshItemRow.run_id)
                .where(
                    QuoteRefreshItemRow.instrument_id.in_(instrument_ids),
                    QuoteRefreshRunRow.status.in_(("completed", "partial")),
                    QuoteRefreshRunRow.finished_at.is_not(None),
                )
                .order_by(
                    QuoteRefreshItemRow.created_at.desc(),
                    QuoteRefreshRunRow.finished_at.desc(),
                    QuoteRefreshItemRow.run_id.desc(),
                )
            )
        ).all()
        latest_attempts: dict[str, QuoteRefreshItemRow] = {}
        for attempt in attempts:
            latest_attempts.setdefault(attempt.instrument_id, attempt)
        return SummaryRecords(
            accounts=accounts,
            positions=positions,
            quotes=quotes,
            latest_attempts=latest_attempts,
        )
