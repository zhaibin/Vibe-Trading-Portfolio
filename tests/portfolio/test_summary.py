from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from vibe_portfolio.portfolio.domain import Currency, QuoteState
from vibe_portfolio.portfolio.service import calculate_summary
from vibe_portfolio.portfolio.tables import AccountRow, LatestQuoteRow, PositionRow, QuoteRefreshItemRow

FIXED_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def account(
    account_id: str,
    *,
    currency: Currency = Currency.CNY,
    cash: str | None = "0",
    archived: bool = False,
) -> AccountRow:
    return AccountRow(
        id=account_id,
        name=f"Account {account_id}",
        normalized_name=f"Account {account_id}",
        currency=currency.value,
        cash_balance=None if cash is None else Decimal(cash),
        version=1,
        created_at=FIXED_NOW - timedelta(days=30),
        updated_at=FIXED_NOW - timedelta(days=1),
        archived_at=FIXED_NOW - timedelta(hours=1) if archived else None,
    )


def position(
    position_id: str,
    account_id: str,
    instrument_id: str,
    *,
    quantity: str,
    average_cost: str,
    archived: bool = False,
) -> PositionRow:
    return PositionRow(
        id=position_id,
        account_id=account_id,
        instrument_id=instrument_id,
        quantity=Decimal(quantity),
        average_cost=Decimal(average_cost),
        note=None,
        version=1,
        created_at=FIXED_NOW - timedelta(days=20),
        updated_at=FIXED_NOW - timedelta(days=1),
        archived_at=FIXED_NOW - timedelta(hours=1) if archived else None,
    )


def quote(
    instrument_id: str,
    *,
    price: str,
    run_id: str,
    currency: Currency = Currency.CNY,
    age: timedelta = timedelta(hours=1),
) -> LatestQuoteRow:
    return LatestQuoteRow(
        instrument_id=instrument_id,
        price=Decimal(price),
        currency=currency.value,
        provider="fake",
        provider_symbol=instrument_id,
        as_of=FIXED_NOW - age,
        fetched_at=FIXED_NOW - timedelta(minutes=30),
        refresh_run_id=run_id,
    )


def attempt(instrument_id: str, *, run_id: str, outcome: str = "updated") -> QuoteRefreshItemRow:
    return QuoteRefreshItemRow(
        run_id=run_id,
        instrument_id=instrument_id,
        outcome=outcome,
        provider="fake" if outcome == "updated" else None,
        error_code=None if outcome == "updated" else "QUOTE_UNAVAILABLE",
        created_at=FIXED_NOW - timedelta(minutes=20),
    )


def test_summary_calculates_exact_fresh_value_cost_pnl_cash_and_allocation() -> None:
    account_row = account("account-cny", cash="100")
    position_row = position("position-cny", account_row.id, "instrument-cny", quantity="10", average_cost="8")
    quote_row = quote(position_row.instrument_id, price="12", run_id="run-fresh")
    attempt_row = attempt(position_row.instrument_id, run_id="run-fresh")

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[position_row],
        quotes={position_row.instrument_id: quote_row},
        latest_attempts={position_row.instrument_id: attempt_row},
        now=FIXED_NOW,
    )

    assert summary.market_value == Decimal("120")
    assert summary.position_cost == Decimal("80")
    assert summary.valued_position_cost == Decimal("80")
    assert summary.unrealized_pnl == Decimal("40")
    assert summary.unrealized_pnl_pct == Decimal("0.5")
    assert summary.known_cash == Decimal("100")
    assert summary.total_value == Decimal("220")
    assert summary.estimated is False
    assert summary.positions[0].quote_state is QuoteState.FRESH
    assert summary.positions[0].allocation == Decimal("1")


def test_summary_excludes_unavailable_value_but_exposes_cost() -> None:
    account_row = account("account-cny", cash=None)
    position_row = position("position-cny", account_row.id, "instrument-cny", quantity="10", average_cost="8")

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[position_row],
        quotes={},
        latest_attempts={},
        now=FIXED_NOW,
    )

    assert summary.market_value == Decimal("0")
    assert summary.position_cost == Decimal("80")
    assert summary.valued_position_cost == Decimal("0")
    assert summary.unvalued_cost == Decimal("80")
    assert summary.unvalued_count == 1
    assert summary.estimated is True
    assert summary.known_cash == Decimal("0")
    assert summary.unknown_cash_account_count == 1
    assert summary.positions[0].quote_state is QuoteState.UNAVAILABLE
    assert summary.positions[0].market_value is None
    assert summary.positions[0].allocation is None


def test_summary_omits_pnl_percentage_when_valued_cost_is_zero() -> None:
    account_row = account("account-cny")
    position_row = position("position-cny", account_row.id, "instrument-cny", quantity="2", average_cost="0")
    quote_row = quote(position_row.instrument_id, price="10", run_id="run-zero-cost")

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[position_row],
        quotes={position_row.instrument_id: quote_row},
        latest_attempts={position_row.instrument_id: attempt(position_row.instrument_id, run_id="run-zero-cost")},
        now=FIXED_NOW,
    )

    assert summary.unrealized_pnl == Decimal("20")
    assert summary.unrealized_pnl_pct is None
    assert summary.positions[0].unrealized_pnl_pct is None


@pytest.mark.parametrize(
    ("quote_age", "outcome"),
    [(timedelta(hours=73), "updated"), (timedelta(hours=1), "stale")],
)
def test_summary_includes_stale_value_and_marks_total_estimated(quote_age: timedelta, outcome: str) -> None:
    account_row = account("account-cny")
    position_row = position("position-cny", account_row.id, "instrument-cny", quantity="3", average_cost="4")
    quote_row = quote(position_row.instrument_id, price="5", run_id="run-stale", age=quote_age)

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[position_row],
        quotes={position_row.instrument_id: quote_row},
        latest_attempts={
            position_row.instrument_id: attempt(position_row.instrument_id, run_id="run-stale", outcome=outcome)
        },
        now=FIXED_NOW,
    )

    assert summary.market_value == Decimal("15")
    assert summary.unvalued_count == 0
    assert summary.stale_count == 1
    assert summary.estimated is True
    assert summary.positions[0].quote_state is QuoteState.STALE


@pytest.mark.parametrize(
    ("age", "expected_state", "estimated"),
    [
        (timedelta(hours=72), QuoteState.FRESH, False),
        (timedelta(hours=72, microseconds=1), QuoteState.STALE, True),
    ],
)
def test_summary_observes_the_exact_72_hour_freshness_boundary(
    age: timedelta,
    expected_state: QuoteState,
    estimated: bool,
) -> None:
    account_row = account("account-cny")
    position_row = position("position-cny", account_row.id, "instrument-cny", quantity="1", average_cost="5")
    quote_row = quote(position_row.instrument_id, price="10", run_id="run-boundary", age=age)

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[position_row],
        quotes={position_row.instrument_id: quote_row},
        latest_attempts={position_row.instrument_id: attempt(position_row.instrument_id, run_id="run-boundary")},
        now=FIXED_NOW,
    )

    assert summary.positions[0].quote_state is expected_state
    assert summary.estimated is estimated


def test_summary_fails_closed_on_a_future_quote() -> None:
    account_row = account("account-cny")
    position_row = position("position-cny", account_row.id, "instrument-cny", quantity="2", average_cost="3")
    quote_row = quote(
        position_row.instrument_id,
        price="10",
        run_id="run-future",
        age=-timedelta(microseconds=1),
    )

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[position_row],
        quotes={position_row.instrument_id: quote_row},
        latest_attempts={position_row.instrument_id: attempt(position_row.instrument_id, run_id="run-future")},
        now=FIXED_NOW,
    )

    assert summary.market_value == Decimal("0")
    assert summary.unvalued_cost == Decimal("6")
    assert summary.unvalued_count == 1
    assert summary.estimated is True
    assert summary.positions[0].quote_state is QuoteState.UNAVAILABLE


@pytest.mark.parametrize("field", ["as_of", "fetched_at"])
def test_summary_fails_closed_on_a_naive_quote_timestamp(field: str) -> None:
    account_row = account("account-cny")
    position_row = position("position-cny", account_row.id, "instrument-cny", quantity="2", average_cost="3")
    quote_row = quote(position_row.instrument_id, price="10", run_id="run-naive")
    setattr(quote_row, field, FIXED_NOW.replace(tzinfo=None))

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[position_row],
        quotes={position_row.instrument_id: quote_row},
        latest_attempts={position_row.instrument_id: attempt(position_row.instrument_id, run_id="run-naive")},
        now=FIXED_NOW,
    )

    assert summary.market_value == Decimal("0")
    assert summary.unvalued_count == 1
    assert summary.estimated is True
    assert summary.positions[0].quote_state is QuoteState.UNAVAILABLE


def test_summary_allocation_excludes_unavailable_positions() -> None:
    account_row = account("account-cny")
    positions = [
        position("position-a", account_row.id, "instrument-a", quantity="1", average_cost="50"),
        position("position-b", account_row.id, "instrument-b", quantity="3", average_cost="50"),
        position("position-c", account_row.id, "instrument-c", quantity="10", average_cost="8"),
    ]
    quotes = {
        "instrument-a": quote("instrument-a", price="100", run_id="run-a"),
        "instrument-b": quote("instrument-b", price="100", run_id="run-b"),
    }
    latest_attempts = {
        "instrument-a": attempt("instrument-a", run_id="run-a"),
        "instrument-b": attempt("instrument-b", run_id="run-b"),
    }

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=positions,
        quotes=quotes,
        latest_attempts=latest_attempts,
        now=FIXED_NOW,
    )

    assert summary.market_value == Decimal("400")
    assert [item.allocation for item in summary.positions] == [Decimal("0.25"), Decimal("0.75"), None]
    assert summary.unvalued_cost == Decimal("80")


def test_summary_pnl_uses_only_valued_cost_when_unavailable_positions_coexist() -> None:
    account_row = account("account-cny")
    valued = position("position-valued", account_row.id, "instrument-valued", quantity="2", average_cost="10")
    unavailable = position(
        "position-unavailable",
        account_row.id,
        "instrument-unavailable",
        quantity="5",
        average_cost="100",
    )
    quote_row = quote(valued.instrument_id, price="15", run_id="run-valued")

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[valued, unavailable],
        quotes={valued.instrument_id: quote_row},
        latest_attempts={valued.instrument_id: attempt(valued.instrument_id, run_id="run-valued")},
        now=FIXED_NOW,
    )

    assert summary.market_value == Decimal("30")
    assert summary.position_cost == Decimal("520")
    assert summary.valued_position_cost == Decimal("20")
    assert summary.unvalued_cost == Decimal("500")
    assert summary.unrealized_pnl == Decimal("10")
    assert summary.unrealized_pnl_pct == Decimal("0.5")
    assert summary.unvalued_count == 1


def test_summary_excludes_archived_rows_and_keeps_currencies_independent() -> None:
    accounts = [
        account("account-cny", currency=Currency.CNY, cash="10"),
        account("account-hkd", currency=Currency.HKD, cash="20"),
        account("account-usd", currency=Currency.USD, cash="30"),
        account("account-archived", currency=Currency.CNY, cash="999", archived=True),
    ]
    positions = [
        position("position-cny", "account-cny", "instrument-cny", quantity="1", average_cost="1"),
        position("position-hkd", "account-hkd", "instrument-hkd", quantity="1", average_cost="1"),
        position("position-usd", "account-usd", "instrument-usd", quantity="1", average_cost="1"),
        position(
            "position-archived",
            "account-cny",
            "instrument-archived",
            quantity="100",
            average_cost="100",
            archived=True,
        ),
    ]
    quotes = {
        instrument_id: quote(
            instrument_id,
            price=price,
            run_id=f"run-{instrument_id}",
            currency=currency,
        )
        for instrument_id, price, currency in (
            ("instrument-cny", "2", Currency.CNY),
            ("instrument-hkd", "3", Currency.HKD),
            ("instrument-usd", "4", Currency.USD),
            ("instrument-archived", "999", Currency.CNY),
        )
    }
    latest_attempts = {
        instrument_id: attempt(instrument_id, run_id=f"run-{instrument_id}") for instrument_id in quotes
    }

    cny = calculate_summary(
        currency=Currency.CNY,
        accounts=accounts,
        positions=positions,
        quotes=quotes,
        latest_attempts=latest_attempts,
        now=FIXED_NOW,
    )
    hkd = calculate_summary(
        currency=Currency.HKD,
        accounts=accounts,
        positions=positions,
        quotes=quotes,
        latest_attempts=latest_attempts,
        now=FIXED_NOW,
    )
    usd = calculate_summary(
        currency=Currency.USD,
        accounts=accounts,
        positions=positions,
        quotes=quotes,
        latest_attempts=latest_attempts,
        now=FIXED_NOW,
    )

    assert (cny.market_value, cny.known_cash, cny.total_value) == (Decimal("2"), Decimal("10"), Decimal("12"))
    assert (hkd.market_value, hkd.known_cash, hkd.total_value) == (Decimal("3"), Decimal("20"), Decimal("23"))
    assert (usd.market_value, usd.known_cash, usd.total_value) == (Decimal("4"), Decimal("30"), Decimal("34"))
    assert cny.account_count == hkd.account_count == usd.account_count == 1
    assert cny.position_count == hkd.position_count == usd.position_count == 1


def test_summary_treats_a_cross_currency_quote_as_unavailable() -> None:
    account_row = account("account-cny", currency=Currency.CNY)
    position_row = position("position-cny", account_row.id, "instrument-cny", quantity="2", average_cost="3")
    quote_row = quote(
        position_row.instrument_id,
        price="10",
        run_id="run-wrong-currency",
        currency=Currency.USD,
    )

    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account_row],
        positions=[position_row],
        quotes={position_row.instrument_id: quote_row},
        latest_attempts={position_row.instrument_id: attempt(position_row.instrument_id, run_id="run-wrong-currency")},
        now=FIXED_NOW,
    )

    assert summary.market_value == Decimal("0")
    assert summary.unvalued_cost == Decimal("6")
    assert summary.positions[0].quote_state is QuoteState.UNAVAILABLE
