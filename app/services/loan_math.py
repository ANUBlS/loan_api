"""Server-side port of the app's lib/utils/loan_math.dart, using Decimal.

The schedule produced here is the source of truth; the app only displays it.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def round2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def annuity_payment(principal: Decimal, annual_rate_percent: Decimal, months: int) -> Decimal:
    """Unrounded monthly annuity payment. Rate is a percent, e.g. 18 for 18%."""
    if months <= 0:
        return Decimal(0)
    principal = Decimal(principal)
    r = Decimal(annual_rate_percent) / Decimal(1200)
    if r == 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


def add_months(d: date, months: int) -> date:
    """Adds calendar months, clamping the day (31 Jan + 1 month = 28/29 Feb)."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


@dataclass(frozen=True)
class ScheduleRow:
    number: int
    due_date: date
    principal: Decimal
    interest: Decimal
    balance_after: Decimal

    @property
    def total(self) -> Decimal:
        return self.principal + self.interest


def build_schedule(
    amount: Decimal, annual_rate: Decimal, term_months: int, start_date: date
) -> list[ScheduleRow]:
    """Annuity schedule; the last installment absorbs rounding differences."""
    amount = Decimal(amount)
    r = Decimal(annual_rate) / Decimal(1200)
    payment = annuity_payment(amount, annual_rate, term_months)
    balance = round2(amount)
    rows: list[ScheduleRow] = []
    for k in range(1, term_months + 1):
        interest = round2(balance * r)
        principal = round2(payment - interest)
        if k == term_months:
            principal = balance
        balance = round2(balance - principal)
        rows.append(
            ScheduleRow(
                number=k,
                due_date=add_months(start_date, k),
                principal=principal,
                interest=interest,
                balance_after=max(balance, ZERO),
            )
        )
    return rows


@dataclass(frozen=True)
class Quote:
    monthly_payment: Decimal
    total_payment: Decimal
    total_interest: Decimal
    schedule: list[ScheduleRow]


def quote(amount: Decimal, annual_rate: Decimal, term_months: int, start_date: date) -> Quote:
    rows = build_schedule(amount, annual_rate, term_months, start_date)
    total = sum((r.total for r in rows), ZERO)
    return Quote(
        monthly_payment=round2(annuity_payment(amount, annual_rate, term_months)),
        total_payment=round2(total),
        total_interest=round2(total - Decimal(amount)),
        schedule=rows,
    )
