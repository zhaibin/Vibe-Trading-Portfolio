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
from vibe_portfolio.portfolio.domain import (
    AssetType,
    Currency,
    DomainValidationError,
    Market,
    canonical_symbol,
    parse_money,
    parse_quantity,
)
from vibe_portfolio.portfolio.repository import (
    InstrumentNotConfirmed,
    PortfolioRepository,
    ReplayUnavailable,
    RepositoryError,
)
from vibe_portfolio.portfolio.schemas import (
    AccountCreate,
    AccountPatch,
    AccountView,
    InstrumentConfirm,
    InstrumentView,
    PositionCreate,
    PositionPatch,
    PositionView,
)
from vibe_portfolio.portfolio.tables import (
    AccountRow,
    AccountVersionRow,
    InstrumentCandidateRow,
    InstrumentRow,
    PositionRow,
    PositionVersionRow,
)

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


def instrument_response(instrument: InstrumentRow) -> dict[str, object]:
    return InstrumentView(
        id=instrument.id,
        canonical_symbol=instrument.canonical_symbol,
        name=instrument.name,
        market=Market(instrument.market),
        currency=Currency(instrument.currency),
        asset_type=AssetType(instrument.asset_type),
        created_at=instrument.created_at,
        updated_at=instrument.updated_at,
    ).model_dump(mode="json")


def position_response(position: PositionRow) -> dict[str, object]:
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
    ).model_dump(mode="json")


def position_version_response(position: PositionVersionRow) -> dict[str, object]:
    try:
        UUID(position.position_id)
        UUID(position.account_id)
        UUID(position.instrument_id)
        view = PositionView.model_validate(
            {
                "id": position.position_id,
                "account_id": position.account_id,
                "instrument_id": position.instrument_id,
                "quantity": parse_quantity(position.quantity),
                "average_cost": parse_money(position.average_cost),
                "note": position.note,
                "version": position.version,
                "created_at": _utc_timestamp(position.created_at),
                "updated_at": _utc_timestamp(position.updated_at),
                "archived_at": None if position.archived_at is None else _utc_timestamp(position.archived_at),
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


def normalize_note(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    if len(normalized) > 500 or any(
        unicodedata.category(character) in {"Cc", "Cs"} for character in normalized
    ):
        raise RepositoryError("VALIDATION_ERROR", fields={"note": "invalid"})
    return normalized


def _candidate_identity(candidate: InstrumentCandidateRow) -> list[tuple[str, str]]:
    try:
        market = Market(candidate.market)
        currency = Currency(candidate.currency)
        AssetType(candidate.asset_type)
        if canonical_symbol(candidate.canonical_symbol, market) != candidate.canonical_symbol:
            raise ValueError("non-canonical symbol")
        expected_currency = {
            Market.CN_SH: Currency.CNY,
            Market.CN_SZ: Currency.CNY,
            Market.CN_BJ: Currency.CNY,
            Market.HK: Currency.HKD,
            Market.US: Currency.USD,
        }[market]
        if currency is not expected_currency or not 1 <= len(candidate.name) <= 160:
            raise ValueError("candidate identity mismatch")
        if any(unicodedata.category(character).startswith("C") for character in candidate.name):
            raise ValueError("candidate name contains controls")
        raw_mappings = json.loads(candidate.provider_symbols_json)
        if not isinstance(raw_mappings, list) or not raw_mappings:
            raise ValueError("provider mappings missing")
        mappings: list[tuple[str, str]] = []
        for raw_mapping in raw_mappings:
            if not isinstance(raw_mapping, dict) or set(raw_mapping) != {"provider", "symbol"}:
                raise ValueError("provider mapping invalid")
            provider = raw_mapping["provider"]
            symbol = raw_mapping["symbol"]
            if (
                not isinstance(provider, str)
                or not isinstance(symbol, str)
                or not 1 <= len(provider) <= 32
                or not 1 <= len(symbol) <= 64
                or any(unicodedata.category(character).startswith("C") for character in provider + symbol)
            ):
                raise ValueError("provider mapping invalid")
            mappings.append((provider, symbol))
        if candidate.provider not in {provider for provider, _ in mappings}:
            raise ValueError("primary provider missing")
    except (DomainValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise InstrumentNotConfirmed() from error
    return mappings


class PortfolioService:
    def __init__(self, database: Database, repository: PortfolioRepository | None = None) -> None:
        self.database, self.repository = database, repository or PortfolioRepository()

    async def create_account(self, command: AccountCreate, key: str) -> PortfolioResponse:
        return await self._write("POST:/api/v1/accounts", key, command, None)

    async def update_account(self, account_id: str, command: AccountPatch, key: str) -> PortfolioResponse:
        return await self._write(f"PATCH:/api/v1/accounts/{account_id}", key, command, account_id)

    async def confirm_instrument(self, command: InstrumentConfirm, key: str) -> PortfolioResponse:
        now = datetime.now(UTC)
        scope = "POST:/api/v1/instruments/confirm"
        async with self.database.session() as session, session.begin():
            request_hash = canonical_request_hash(command.model_dump(mode="json"))
            claim = await self.repository.claim_idempotency(session, scope, key, request_hash, now)
            if claim.completed:
                resource_id = claim.row.resource_id
                if (
                    claim.row.response_status != 201
                    or claim.row.resource_version != 1
                    or not isinstance(resource_id, str)
                ):
                    raise ReplayUnavailable()
                instrument = await self.repository.instrument(session, resource_id)
                if instrument is None:
                    raise ReplayUnavailable()
                return PortfolioResponse(201, instrument_response(instrument))
            candidate_id = str(command.candidate_id)
            candidate = await self.repository.candidate(session, candidate_id, now)
            if candidate is None:
                raise InstrumentNotConfirmed()
            mappings = _candidate_identity(candidate)
            instrument = await self.repository.upsert_instrument(session, candidate, mappings, now)
            await self.repository.consume_candidate(session, candidate_id, now)
            await self.repository.complete_resource_idempotency(
                session,
                claim,
                resource_id=instrument.id,
                resource_version=1,
                status=201,
            )
            return PortfolioResponse(201, instrument_response(instrument))

    async def create_position(self, command: PositionCreate, key: str) -> PortfolioResponse:
        return await self._write_position("POST:/api/v1/positions", key, command, None)

    async def update_position(self, position_id: str, command: PositionPatch, key: str) -> PortfolioResponse:
        return await self._write_position(f"PATCH:/api/v1/positions/{position_id}", key, command, position_id)

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

    async def _write_position(
        self,
        scope: str,
        key: str,
        command: PositionCreate | PositionPatch,
        position_id: str | None,
    ) -> PortfolioResponse:
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            request_hash = canonical_request_hash(command.model_dump(mode="json", exclude_unset=True))
            claim = await self.repository.claim_idempotency(session, scope, key, request_hash, now)
            expected_status = 201 if isinstance(command, PositionCreate) else 200
            if claim.completed:
                resource_id = claim.row.resource_id
                resource_version = claim.row.resource_version
                if (
                    claim.row.response_status != expected_status
                    or not isinstance(resource_id, str)
                    or isinstance(resource_version, bool)
                    or not isinstance(resource_version, int)
                    or resource_version < 1
                    or (position_id is not None and resource_id != position_id)
                ):
                    raise ReplayUnavailable()
                history = await self.repository.position_version(session, resource_id, resource_version)
                if history is None:
                    raise ReplayUnavailable()
                return PortfolioResponse(expected_status, position_version_response(history))
            if isinstance(command, PositionCreate):
                position = await self.repository.create_position(
                    session,
                    command,
                    normalize_note(command.note),
                    now,
                )
            else:
                assert position_id is not None
                note = normalize_note(command.note) if "note" in command.model_fields_set else None
                position = await self.repository.update_position(session, position_id, command, note, now)
            response = position_response(position)
            await self.repository.record_position_version(session, position)
            await self.repository.complete_resource_idempotency(
                session,
                claim,
                resource_id=position.id,
                resource_version=position.version,
                status=expected_status,
            )
            return PortfolioResponse(expected_status, response)

    async def list_positions(
        self,
        *,
        archived: bool,
        account_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[PositionRow], str | None]:
        async with self.database.session() as session:
            rows = await self.repository.list_positions(
                session,
                archived=archived,
                account_id=account_id,
                cursor=cursor,
                limit=limit,
            )
        page = rows[:limit]
        return page, page[-1].id if len(rows) > limit else None
