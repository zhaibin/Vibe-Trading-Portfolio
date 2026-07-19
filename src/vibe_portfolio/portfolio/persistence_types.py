"""SQLite-safe SQLAlchemy types for exact portfolio persistence."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.engine import Dialect
from sqlalchemy.types import Text, TypeDecorator


class ExactDecimal(TypeDecorator[Decimal]):
    """Persist Decimal values as their exact fixed-point text representation."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        del dialect
        return None if value is None else format(value, "f")

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        del dialect
        return None if value is None else Decimal(value)


class UtcIsoDateTime(TypeDecorator[datetime]):
    """Persist aware timestamps as canonical UTC ISO-8601 text."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat()

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("stored timestamp is not timezone-aware")
        return parsed.astimezone(UTC)
