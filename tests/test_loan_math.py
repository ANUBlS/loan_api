from datetime import date
from decimal import Decimal

from app.services.loan_math import add_months, annuity_payment, build_schedule, quote, round2


def test_zero_rate_annuity_splits_evenly():
    assert annuity_payment(Decimal(12000), Decimal(0), 12) == 1000


def test_schedule_principal_sums_to_amount():
    rows = build_schedule(Decimal(5000), Decimal(18), 24, date(2025, 1, 31))
    assert sum(r.principal for r in rows) == Decimal("5000.00")
    assert rows[-1].balance_after == 0
    assert rows[0].due_date == date(2025, 2, 28)  # day clamped
    assert rows[1].due_date == date(2025, 3, 31)  # back to the 31st


def test_monthly_payment_matches_app():
    # 5000 AZN, 18%, 24 months -> 249.62 (same as the Flutter app)
    assert round2(annuity_payment(Decimal(5000), Decimal(18), 24)) == Decimal("249.62")


def test_add_months_across_years():
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert add_months(date(2026, 1, 15), -2) == date(2025, 11, 15)


def test_quote_totals():
    q = quote(Decimal(10000), Decimal(17), 12, date(2026, 1, 1))
    assert q.total_payment == sum(r.total for r in q.schedule)
    assert q.total_interest == q.total_payment - Decimal(10000)
