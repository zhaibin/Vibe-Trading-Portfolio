"""Move exact account replay state out of idempotency storage."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import RowMapping

revision: str = "20260719_0003"
down_revision: str | Sequence[str] | None = "20260719_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC).isoformat()


def _money(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("money must be a string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("money is invalid") from error
    exponent = parsed.as_tuple().exponent
    if (
        not parsed.is_finite()
        or parsed < 0
        or parsed > Decimal("1000000000000")
        or (isinstance(exponent, int) and -exponent > 6)
    ):
        raise ValueError("money is outside the supported domain")
    return format(parsed, "f")


def _snapshot(row: RowMapping) -> dict[str, object] | None:
    raw = row["response_json"]
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    account_id = value.get("id")
    version = value.get("version")
    name = value.get("name")
    currency = value.get("currency")
    try:
        cash_balance = _money(value.get("cash_balance"))
    except ValueError:
        return None
    created_at = _timestamp(value.get("created_at"))
    updated_at = _timestamp(value.get("updated_at"))
    archived_value = value.get("archived_at")
    archived_at = None if archived_value is None else _timestamp(archived_value)
    try:
        if isinstance(account_id, str):
            UUID(account_id)
        else:
            return None
    except ValueError:
        return None
    if (
        account_id != row["resource_id"]
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(name, str)
        or not 1 <= len(name) <= 80
        or currency not in {"CNY", "HKD", "USD"}
        or created_at is None
        or updated_at is None
        or (archived_value is not None and archived_at is None)
    ):
        return None
    return {
        "account_id": account_id,
        "version": version,
        "name": name,
        "currency": currency,
        "cash_balance": cash_balance,
        "created_at": created_at,
        "updated_at": updated_at,
        "archived_at": archived_at,
    }


def upgrade() -> None:
    op.add_column("idempotency_records", sa.Column("resource_version", sa.Integer(), nullable=True))
    op.create_table(
        "account_versions",
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("cash_balance", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 80", name="ck_account_versions_name"),
        sa.CheckConstraint("currency IN ('CNY', 'HKD', 'USD')", name="ck_account_versions_currency"),
        sa.CheckConstraint("version >= 1", name="ck_account_versions_version"),
    )

    connection = op.get_bind()
    completed = connection.execute(
        sa.text(
            "SELECT scope, key_hash, resource_id, response_status, response_json "
            "FROM idempotency_records WHERE state = 'completed' ORDER BY scope, key_hash"
        )
    ).mappings()
    for row in completed:
        expected_status = 201 if row["scope"] == "POST:/api/v1/accounts" else 200
        if row["response_status"] != expected_status:
            continue
        snapshot = _snapshot(row)
        if snapshot is None:
            continue
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO account_versions "
                "(account_id, version, name, currency, cash_balance, created_at, updated_at, archived_at) "
                "VALUES "
                "(:account_id, :version, :name, :currency, :cash_balance, :created_at, :updated_at, :archived_at)"
            ),
            snapshot,
        )
        stored = connection.execute(
            sa.text(
                "SELECT account_id, version, name, currency, cash_balance, created_at, updated_at, archived_at "
                "FROM account_versions WHERE account_id = :account_id AND version = :version"
            ),
            {"account_id": snapshot["account_id"], "version": snapshot["version"]},
        ).mappings().one_or_none()
        if stored is None or any(stored[field] != value for field, value in snapshot.items()):
            continue
        connection.execute(
            sa.text(
                "UPDATE idempotency_records SET resource_version = :version "
                "WHERE scope = :scope AND key_hash = :key_hash"
            ),
            {"version": snapshot["version"], "scope": row["scope"], "key_hash": row["key_hash"]},
        )

    connection.execute(
        sa.text(
            "INSERT OR IGNORE INTO account_versions "
            "(account_id, version, name, currency, cash_balance, created_at, updated_at, archived_at) "
            "SELECT id, version, name, currency, cash_balance, created_at, updated_at, archived_at FROM accounts"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE idempotency_records SET resource_id = NULL, resource_version = NULL, response_status = NULL "
            "WHERE state = 'pending'"
        )
    )
    op.drop_column("idempotency_records", "response_json")


def downgrade() -> None:
    op.add_column("idempotency_records", sa.Column("response_json", sa.Text(), nullable=True))
    op.drop_table("account_versions")
    op.drop_column("idempotency_records", "resource_version")
