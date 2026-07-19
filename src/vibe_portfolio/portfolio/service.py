"""Portfolio application rules and transaction boundaries."""

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError

from vibe_portfolio.portfolio.database import Database
from vibe_portfolio.portfolio.domain import (
    AssetType,
    Currency,
    DomainValidationError,
    Market,
    QuoteState,
    canonical_symbol,
    parse_money,
    parse_quantity,
    quote_state,
)
from vibe_portfolio.portfolio.repository import (
    CANDIDATE_TTL,
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
    PortfolioSummary,
    PositionCreate,
    PositionPatch,
    PositionView,
    SummaryPosition,
)
from vibe_portfolio.portfolio.tables import (
    AccountRow,
    AccountVersionRow,
    InstrumentCandidateRow,
    InstrumentRow,
    LatestQuoteRow,
    PositionRow,
    PositionVersionRow,
    QuoteRefreshItemRow,
)

_WHITESPACE = re.compile(r"\s+")
_MONEY_QUANTUM = Decimal("0.000001")
_QUANTITY_QUANTUM = Decimal("0.00000001")
_RATIO_QUANTUM = Decimal("0.000001")
_CALCULATION_PRECISION = 60


@dataclass(frozen=True, slots=True)
class PortfolioResponse:
    status: int
    body: dict[str, object]


@dataclass(frozen=True, slots=True)
class _CalculatedPosition:
    position: PositionRow
    position_cost: Decimal
    quote: LatestQuoteRow | None
    state: QuoteState
    market_value: Decimal | None
    unrealized_pnl: Decimal | None


def _quantize_display(value: Decimal, quantum: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _CALCULATION_PRECISION
        return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def display_money(value: Decimal) -> Decimal:
    return _quantize_display(value, _MONEY_QUANTUM)


def display_quantity(value: Decimal) -> Decimal:
    return _quantize_display(value, _QUANTITY_QUANTUM)


def display_ratio(value: Decimal) -> Decimal:
    return _quantize_display(value, _RATIO_QUANTUM)


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def calculate_summary(
    *,
    currency: Currency,
    accounts: Sequence[AccountRow],
    positions: Sequence[PositionRow],
    quotes: Mapping[str, LatestQuoteRow],
    latest_attempts: Mapping[str, QuoteRefreshItemRow],
    now: datetime,
) -> PortfolioSummary:
    if not _aware(now):
        raise ValueError("summary clock must be timezone-aware")

    active_accounts = {
        account.id: account
        for account in accounts
        if account.archived_at is None and account.currency == currency.value
    }
    active_positions = [
        position
        for position in positions
        if position.archived_at is None and position.account_id in active_accounts
    ]

    with localcontext() as context:
        context.prec = _CALCULATION_PRECISION
        known_cash = sum(
            (account.cash_balance for account in active_accounts.values() if account.cash_balance is not None),
            Decimal("0"),
        )
        unknown_cash_count = sum(account.cash_balance is None for account in active_accounts.values())
        position_cost = Decimal("0")
        market_value = Decimal("0")
        valued_position_cost = Decimal("0")
        stale_count = 0
        unvalued_count = 0
        calculated: list[_CalculatedPosition] = []

        for position in active_positions:
            cost = position.quantity * position.average_cost
            position_cost += cost
            candidate_quote = quotes.get(position.instrument_id)
            valid_quote = (
                candidate_quote is not None
                and candidate_quote.currency == currency.value
                and candidate_quote.price > 0
                and _aware(candidate_quote.as_of)
                and _aware(candidate_quote.fetched_at)
                and candidate_quote.as_of <= now
            )
            if not valid_quote:
                unvalued_count += 1
                calculated.append(
                    _CalculatedPosition(position, cost, None, QuoteState.UNAVAILABLE, None, None)
                )
                continue

            assert candidate_quote is not None
            latest_attempt = latest_attempts.get(position.instrument_id)
            attempt_succeeded = (
                latest_attempt is not None
                and latest_attempt.outcome == "updated"
                and latest_attempt.run_id == candidate_quote.refresh_run_id
            )
            state = quote_state(candidate_quote.as_of, latest_attempt_succeeded=attempt_succeeded, now=now)
            value = position.quantity * candidate_quote.price
            pnl = value - cost
            market_value += value
            valued_position_cost += cost
            if state is QuoteState.STALE:
                stale_count += 1
            calculated.append(_CalculatedPosition(position, cost, candidate_quote, state, value, pnl))

        aggregate_pnl = market_value - valued_position_cost
        aggregate_pnl_pct = (
            None if valued_position_cost == 0 else display_ratio(aggregate_pnl / valued_position_cost)
        )
        total_value = market_value + known_cash

        summary_positions: list[SummaryPosition] = []
        for item in calculated:
            row_pnl_pct = (
                None
                if item.unrealized_pnl is None or item.position_cost == 0
                else display_ratio(item.unrealized_pnl / item.position_cost)
            )
            allocation = (
                None
                if item.market_value is None or market_value == 0
                else display_ratio(item.market_value / market_value)
            )
            quote_row = item.quote
            summary_positions.append(
                SummaryPosition(
                    position_id=item.position.id,
                    account_id=item.position.account_id,
                    instrument_id=item.position.instrument_id,
                    quantity=display_quantity(item.position.quantity),
                    average_cost=display_money(item.position.average_cost),
                    position_cost=display_money(item.position_cost),
                    quote_price=None if quote_row is None else display_money(quote_row.price),
                    market_value=None if item.market_value is None else display_money(item.market_value),
                    unrealized_pnl=None if item.unrealized_pnl is None else display_money(item.unrealized_pnl),
                    unrealized_pnl_pct=row_pnl_pct,
                    allocation=allocation,
                    quote_state=item.state,
                    quote_provider=None if quote_row is None else quote_row.provider,
                    quote_as_of=None if quote_row is None else quote_row.as_of,
                    quote_fetched_at=None if quote_row is None else quote_row.fetched_at,
                )
            )

    return PortfolioSummary(
        currency=currency,
        account_count=len(active_accounts),
        position_count=len(active_positions),
        valued_count=len(active_positions) - unvalued_count,
        stale_count=stale_count,
        unvalued_count=unvalued_count,
        market_value=display_money(market_value),
        position_cost=display_money(position_cost),
        valued_position_cost=display_money(valued_position_cost),
        unvalued_cost=display_money(position_cost - valued_position_cost),
        unrealized_pnl=display_money(aggregate_pnl),
        unrealized_pnl_pct=aggregate_pnl_pct,
        known_cash=display_money(known_cash),
        unknown_cash_account_count=unknown_cash_count,
        total_value=display_money(total_value),
        estimated=unknown_cash_count > 0 or stale_count > 0 or unvalued_count > 0,
        positions=summary_positions,
    )


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
        note = normalize_note(position.note)
        if note != position.note:
            raise ValueError("stored note is not canonical")
        created_at = _utc_timestamp(position.created_at)
        updated_at = _utc_timestamp(position.updated_at)
        archived_at = None if position.archived_at is None else _utc_timestamp(position.archived_at)
        if created_at > updated_at or (
            archived_at is not None and (created_at > archived_at or updated_at > archived_at)
        ):
            raise ValueError("stored timestamps are not chronological")
        view = PositionView.model_validate(
            {
                "id": position.position_id,
                "account_id": position.account_id,
                "instrument_id": position.instrument_id,
                "quantity": parse_quantity(position.quantity),
                "average_cost": parse_money(position.average_cost),
                "note": note,
                "version": position.version,
                "created_at": created_at,
                "updated_at": updated_at,
                "archived_at": archived_at,
            }
        )
    except (DomainValidationError, RepositoryError, TypeError, ValueError, ValidationError) as error:
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
    if len(normalized) > 500 or any(_unsafe_note_character(character) for character in normalized):
        raise RepositoryError("VALIDATION_ERROR", fields={"note": "invalid"})
    return normalized


def _unsafe_note_character(character: str) -> bool:
    category = unicodedata.category(character)
    return category in {"Cc", "Cs"} or (category == "Cf" and character != "\u200d")


def _candidate_identity(candidate: InstrumentCandidateRow, now: datetime) -> list[tuple[str, str]]:
    try:
        if not (
            candidate.created_at <= now < candidate.expires_at
            and candidate.expires_at <= candidate.created_at + CANDIDATE_TTL
        ):
            raise ValueError("candidate lifetime invalid")
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
            mappings = _candidate_identity(candidate, now)
            instrument = await self.repository.upsert_instrument(session, candidate, mappings, now)
            await self.repository.consume_candidate(session, candidate_id, now)
            await self.repository.complete_resource_idempotency(
                session,
                claim,
                resource_id=instrument.id,
                resource_version=1,
                status=201,
            )
            await self.repository.prune_expired(session, now)
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
            await self.repository.prune_expired(session, now)
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
            await self.repository.prune_expired(session, now)
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

    async def summary(self, currency: Currency, now: datetime) -> PortfolioSummary:
        async with self.database.session() as session:
            records = await self.repository.summary_records(session, currency.value)
        return calculate_summary(
            currency=currency,
            accounts=records.accounts,
            positions=records.positions,
            quotes=records.quotes,
            latest_attempts=records.latest_attempts,
            now=now,
        )
