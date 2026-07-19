"""HTTP schemas for the local current-position portfolio API."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, TypeAlias, TypeVar

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer, WithJsonSchema

from vibe_portfolio.portfolio.domain import Currency, DomainValidationError, parse_money, parse_price, parse_quantity


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
    name: str = Field(min_length=1, max_length=80)
    currency: Currency
    cash_balance: MoneyInput | None = None


class AccountPatch(BaseModel):
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


T = TypeVar("T")


class CursorPage(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


class ErrorDetail(BaseModel):
    code: str
    fields: dict[str, object] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail
