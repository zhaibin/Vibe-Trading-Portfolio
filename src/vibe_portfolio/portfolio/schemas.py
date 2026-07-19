"""HTTP schemas for the local current-position portfolio API."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, Self, TypeAlias, TypeVar
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, WithJsonSchema, model_validator

from vibe_portfolio.portfolio.domain import (
    AssetType,
    Currency,
    DomainValidationError,
    Market,
    QuoteState,
    parse_money,
    parse_price,
    parse_quantity,
)


def _exact_input(parser: Callable[[str], Decimal]) -> Callable[[object], Decimal]:
    def validate(value: object) -> Decimal:
        if not isinstance(value, str):
            raise ValueError("decimal_string_required")
        try:
            return parser(value)
        except DomainValidationError as error:
            raise ValueError(str(error)) from error

    return validate


MoneyInput: TypeAlias = Annotated[
    Decimal,
    BeforeValidator(_exact_input(parse_money)),
    PlainSerializer(lambda value: format(value, "f"), return_type=str),
    WithJsonSchema({"type": "string"}),
]
QuantityInput: TypeAlias = Annotated[
    Decimal,
    BeforeValidator(_exact_input(parse_quantity)),
    PlainSerializer(lambda value: format(value, "f"), return_type=str),
    WithJsonSchema({"type": "string"}),
]
PriceInput: TypeAlias = Annotated[
    Decimal,
    BeforeValidator(_exact_input(parse_price)),
    PlainSerializer(lambda value: format(value, "f"), return_type=str),
    WithJsonSchema({"type": "string"}),
]
DecimalOutput = Annotated[
    Decimal,
    PlainSerializer(lambda value: format(value, "f"), return_type=str),
    WithJsonSchema({"type": "string"}),
]


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    currency: Currency
    cash_balance: MoneyInput | None = None


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    cash_balance: MoneyInput | None = None
    archived: bool | None = None


class AccountView(BaseModel):
    id: str
    name: str
    currency: Currency
    cash_balance: DecimalOutput | None
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class InstrumentConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: UUID


class InstrumentView(BaseModel):
    id: str
    canonical_symbol: str
    name: str
    market: Market
    currency: Currency
    asset_type: AssetType
    created_at: datetime
    updated_at: datetime


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_ids: list[UUID] | None = None


class RefreshItemView(BaseModel):
    instrument_id: UUID
    outcome: Literal["updated", "stale", "unavailable"]
    provider: str | None
    error_code: str | None


class RefreshRunView(BaseModel):
    run_id: UUID
    status: Literal["running", "succeeded", "partial", "failed"]
    updated: int
    stale: int
    unavailable: int
    terminal_error: str | None
    providers_used: tuple[str, ...]
    started_at: datetime
    finished_at: datetime | None
    items: list[RefreshItemView]


class PositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: UUID
    instrument_id: UUID
    quantity: QuantityInput
    average_cost: MoneyInput
    note: str | None = None


class PositionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    quantity: QuantityInput | None = None
    average_cost: MoneyInput | None = None
    note: str | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def reject_null_numeric_updates(self) -> Self:
        for field in ("quantity", "average_cost"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field}_required")
        return self


class PositionView(BaseModel):
    id: str
    account_id: str
    instrument_id: str
    quantity: DecimalOutput
    average_cost: DecimalOutput
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class SummaryPosition(BaseModel):
    position_id: str
    account_id: str
    instrument_id: str
    quantity: DecimalOutput
    average_cost: DecimalOutput
    position_cost: DecimalOutput
    quote_price: DecimalOutput | None
    market_value: DecimalOutput | None
    unrealized_pnl: DecimalOutput | None
    unrealized_pnl_pct: DecimalOutput | None
    allocation: DecimalOutput | None
    quote_state: QuoteState
    quote_provider: str | None
    quote_as_of: datetime | None
    quote_fetched_at: datetime | None


class PortfolioSummary(BaseModel):
    currency: Currency
    account_count: int
    position_count: int
    valued_count: int
    stale_count: int
    unvalued_count: int
    market_value: DecimalOutput
    position_cost: DecimalOutput
    valued_position_cost: DecimalOutput
    unvalued_cost: DecimalOutput
    unrealized_pnl: DecimalOutput
    unrealized_pnl_pct: DecimalOutput | None
    known_cash: DecimalOutput
    unknown_cash_account_count: int
    total_value: DecimalOutput
    estimated: bool
    positions: list[SummaryPosition]


T = TypeVar("T")


class CursorPage(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


class ErrorDetail(BaseModel):
    code: str
    fields: dict[str, object] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail
