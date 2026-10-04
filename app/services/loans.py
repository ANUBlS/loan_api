"""Loan read models, loan creation and payments."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .. import schemas
from ..config import settings
from ..errors import ApiError
from ..models import Installment, Loan, LoanProduct, Payment, PaymentMethod, User
from ..timeutils import now_utc, today
from . import documents, loan_math
from .numbering import next_contract_no, next_payment_ref

ZERO = Decimal("0.00")
_STATE_ORDER = {"overdue": 0, "active": 1, "closed": 2}


# ---------------------------------------------------------------- read model


def installment_status(inst: Installment, next_number: int | None, on: date) -> str:
    if inst.is_paid:
        return "paid"
    if inst.due_date < on:
        return "overdue"
    if next_number == inst.number:
        return "next"
    return "upcoming"


def _inst_out(inst: Installment, next_number: int | None, on: date) -> schemas.InstallmentOut:
    return schemas.InstallmentOut(
        number=inst.number,
        due_date=inst.due_date,
        principal=inst.principal,
        interest=inst.interest,
        total=inst.total,
        balance_after=inst.balance_after,
        paid_date=inst.paid_at,
        status=installment_status(inst, next_number, on),
    )


def loan_state(loan: Loan, on: date) -> str:
    sched = loan.installments
    if all(i.is_paid for i in sched):
        return "closed"
    if any(not i.is_paid and i.due_date < on for i in sched):
        return "overdue"
    return "active"


def _summary_fields(loan: Loan, on: date) -> dict:
    sched = loan.installments
    unpaid = [i for i in sched if not i.is_paid]
    paid = [i for i in sched if i.is_paid]
    overdue = [i for i in unpaid if i.due_date < on]
    nxt = next((i for i in unpaid if i.due_date >= on), None)
    next_no = nxt.number if nxt else None
    outstanding = sum((i.principal for i in unpaid), ZERO)
    return dict(
        id=loan.id,
        type=loan.type,
        product_name=loan.product_name_key,
        contract_no=loan.contract_no,
        currency=loan.currency,
        amount=loan.amount,
        annual_rate=loan.annual_rate,
        term_months=loan.term_months,
        start_date=loan.start_date,
        final_payment_date=sched[-1].due_date if sched else None,
        state=loan_state(loan, on),
        monthly_payment=sched[0].total if sched else ZERO,
        paid_count=len(paid),
        paid_total=sum((i.total for i in paid), ZERO),
        paid_principal=loan.amount - outstanding,
        outstanding_principal=outstanding,
        overdue_count=len(overdue),
        overdue_amount=sum((i.total for i in overdue), ZERO),
        first_unpaid=_inst_out(unpaid[0], next_no, on) if unpaid else None,
        next_installment=_inst_out(nxt, next_no, on) if nxt else None,
    )


def loan_summary(loan: Loan, on: date | None = None) -> schemas.LoanSummaryOut:
    return schemas.LoanSummaryOut(**_summary_fields(loan, on or today()))


def loan_detail(loan: Loan, on: date | None = None) -> schemas.LoanDetailOut:
    on = on or today()
    fields = _summary_fields(loan, on)
    nxt = fields["next_installment"]
    next_no = nxt.number if nxt else None
    return schemas.LoanDetailOut(
        **fields, schedule=[_inst_out(i, next_no, on) for i in loan.installments]
    )


def list_loans(db: Session, user: User) -> schemas.LoanListOut:
    loans = db.scalars(
        select(Loan)
        .where(Loan.user_id == user.id)
        .options(selectinload(Loan.installments))
        .order_by(Loan.start_date.desc())
    ).all()
    on = today()
    items = [loan_summary(l, on) for l in loans]
    items.sort(key=lambda s: _STATE_ORDER[s.state])  # stable: overdue, active, closed

    urgent = next((s for s in items if s.state == "overdue"), None)
    if urgent is None:
        open_loans = [s for s in items if s.state == "active" and s.first_unpaid]
        open_loans.sort(key=lambda s: s.first_unpaid.due_date)
        urgent = open_loans[0] if open_loans else None
    return schemas.LoanListOut(items=items, urgent_loan_id=urgent.id if urgent else None)


def get_user_loan(db: Session, user: User, loan_id: uuid.UUID, *, lock: bool = False) -> Loan:
    stmt = select(Loan).where(Loan.id == loan_id, Loan.user_id == user.id)
    if lock:
        stmt = stmt.with_for_update()
    else:
        stmt = stmt.options(selectinload(Loan.installments))
    loan = db.scalar(stmt)
    if loan is None:
        raise ApiError(404, "loan_not_found", "Loan not found")
    return loan


# ------------------------------------------------------------------ creation


def create_loan(
    db: Session,
    *,
    user: User,
    product: LoanProduct,
    amount: Decimal,
    annual_rate: Decimal,
    term_months: int,
    start_date: date,
    product_name_key: str | None = None,
    contract_no: str | None = None,
    application_id: uuid.UUID | None = None,
) -> Loan:
    """Creates the loan, its annuity schedule and its documents (no commit)."""
    loan = Loan(
        user_id=user.id,
        product_id=product.id,
        application_id=application_id,
        type=product.type,
        product_name_key=product_name_key or product.loan_name_key,
        contract_no=contract_no or next_contract_no(db, product.type, start_date.year),
        currency=settings.currency,
        amount=loan_math.round2(amount),
        annual_rate=Decimal(annual_rate),
        term_months=term_months,
        start_date=start_date,
    )
    for row in loan_math.build_schedule(amount, annual_rate, term_months, start_date):
        loan.installments.append(
            Installment(
                number=row.number,
                due_date=row.due_date,
                principal=row.principal,
                interest=row.interest,
                balance_after=row.balance_after,
            )
        )
    db.add(loan)
    db.flush()
    documents.generate_for_loan(db, loan, user)
    return loan


# ------------------------------------------------------------------ payments


def payment_out(p: Payment) -> schemas.PaymentOut:
    return schemas.PaymentOut(
        id=p.id,
        reference=p.reference,
        loan_id=p.loan_id,
        contract_no=p.loan.contract_no,
        product_name=p.loan.product_name_key,
        installment_number=p.installment.number,
        amount=p.amount,
        currency=p.currency,
        paid_at=p.paid_at,
    )


def record_payment(
    db: Session, user: User, loan: Loan, inst: Installment,
    paid_at=None, idempotency_key: str | None = None,
    method: PaymentMethod = PaymentMethod.app, note: str | None = None,
    admin_id: uuid.UUID | None = None,
) -> Payment:
    paid_at = paid_at or now_utc()
    inst.paid_at = paid_at
    payment = Payment(
        reference=next_payment_ref(db),
        user_id=user.id,
        loan_id=loan.id,
        installment_id=inst.id,
        amount=inst.total,
        currency=loan.currency,
        paid_at=paid_at,
        idempotency_key=idempotency_key,
        method=method,
        note=note,
        created_by_admin_id=admin_id,
    )
    db.add(payment)
    return payment


def _existing_payment(db: Session, user: User, key: str) -> Payment | None:
    return db.scalar(
        select(Payment)
        .where(Payment.user_id == user.id, Payment.idempotency_key == key)
        .options(selectinload(Payment.loan), selectinload(Payment.installment))
    )


def pay_next(
    db: Session, user: User, loan_id: uuid.UUID, idempotency_key: str | None
) -> tuple[schemas.PayNextOut, bool]:
    """Pays the earliest unpaid installment (overdue ones first).

    Returns (result, created). With the same Idempotency-Key the original
    payment is returned instead of paying a second installment.
    """
    if idempotency_key:
        prev = _existing_payment(db, user, idempotency_key)
        if prev is not None:
            if prev.loan_id != loan_id:
                raise ApiError(409, "idempotency_conflict",
                               "Idempotency-Key already used for another loan")
            loan = get_user_loan(db, user, loan_id)
            return schemas.PayNextOut(payment=payment_out(prev), loan=loan_summary(loan)), False

    # Row lock: two taps on "Pay" can never pay two installments at once.
    loan = get_user_loan(db, user, loan_id, lock=True)
    inst = db.scalar(
        select(Installment)
        .where(Installment.loan_id == loan.id, Installment.paid_at.is_(None))
        .order_by(Installment.number)
        .limit(1)
        .with_for_update()
    )
    if inst is None:
        raise ApiError(409, "loan_closed", "Loan is fully repaid")

    payment = record_payment(db, user, loan, inst, idempotency_key=idempotency_key)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key and (prev := _existing_payment(db, user, idempotency_key)):
            loan = get_user_loan(db, user, loan_id)
            return schemas.PayNextOut(payment=payment_out(prev), loan=loan_summary(loan)), False
        raise

    db.refresh(payment)
    loan = get_user_loan(db, user, loan_id)
    db.refresh(loan, ["installments"])
    payment = db.scalar(
        select(Payment).where(Payment.id == payment.id)
        .options(selectinload(Payment.loan), selectinload(Payment.installment))
    )
    return schemas.PayNextOut(payment=payment_out(payment), loan=loan_summary(loan)), True


def payment_history(
    db: Session, user: User, limit: int, offset: int, loan_id: uuid.UUID | None
) -> schemas.PaymentPageOut:
    where = [Payment.user_id == user.id, Payment.reversed_at.is_(None)]
    if loan_id:
        where.append(Payment.loan_id == loan_id)
    total = db.scalar(select(func.count()).select_from(Payment).where(*where)) or 0
    rows = db.scalars(
        select(Payment)
        .where(*where)
        .options(selectinload(Payment.loan), selectinload(Payment.installment))
        .order_by(Payment.paid_at.desc(), Payment.reference.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return schemas.PaymentPageOut(
        items=[payment_out(p) for p in rows], total=total, limit=limit, offset=offset
    )
