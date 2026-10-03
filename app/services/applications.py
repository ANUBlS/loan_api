import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import schemas
from ..config import settings
from ..errors import ApiError
from ..models import ApplicationStatus, Loan, LoanApplication, LoanProduct, User
from ..timeutils import now_utc, today
from . import loan_math
from .loans import create_loan
from .numbering import next_application_ref

# Same translation keys as the app's MockData.purposes
PURPOSES = [
    "purpose.personal",
    "purpose.car",
    "purpose.home",
    "purpose.renovation",
    "purpose.education",
    "purpose.business",
]


def active_products(db: Session) -> list[LoanProduct]:
    return list(db.scalars(
        select(LoanProduct).where(LoanProduct.is_active.is_(True))
        .order_by(LoanProduct.sort_order, LoanProduct.id)
    ))


def get_active_product(db: Session, product_id: int) -> LoanProduct:
    p = db.get(LoanProduct, product_id)
    if p is None or not p.is_active:
        raise ApiError(404, "product_not_found", "Loan product not found")
    return p


def validate_terms(p: LoanProduct, amount: Decimal, term: int) -> None:
    if not (p.min_amount <= amount <= p.max_amount):
        raise ApiError(422, "amount_out_of_range", "Amount is outside the product limits",
                       details={"min": float(p.min_amount), "max": float(p.max_amount)})
    if (amount - p.min_amount) % p.step != 0:
        raise ApiError(422, "amount_step", f"Amount must change in steps of {p.step}",
                       details={"step": float(p.step)})
    if not (p.min_term <= term <= p.max_term):
        raise ApiError(422, "term_out_of_range", "Term is outside the product limits",
                       details={"min": p.min_term, "max": p.max_term})


def application_out(a: LoanApplication) -> schemas.ApplicationOut:
    return schemas.ApplicationOut(
        id=a.id, reference=a.reference, type=a.type, product_name=a.product_name_key,
        amount=a.amount, term_months=a.term_months, annual_rate=a.annual_rate,
        monthly_payment=a.monthly_payment, purpose=a.purpose, currency=a.currency,
        status=a.status, decision_note=a.decision_note, created_at=a.created_at,
        decided_at=a.decided_at, loan_id=a.loan.id if a.loan else None,
    )


def admin_application_out(a: LoanApplication) -> schemas.AdminApplicationOut:
    return schemas.AdminApplicationOut(
        **application_out(a).model_dump(),
        user_id=a.user_id, user_phone=a.user.phone, user_full_name=a.user.full_name,
    )


def submit(db: Session, user: User, data: schemas.ApplicationIn) -> LoanApplication:
    p = get_active_product(db, data.product_id)
    validate_terms(p, data.amount, data.term_months)
    if data.purpose not in PURPOSES:
        raise ApiError(422, "purpose_invalid", "Unknown loan purpose",
                       details={"allowed": PURPOSES})

    pending = db.scalar(
        select(func.count()).select_from(LoanApplication).where(
            LoanApplication.user_id == user.id,
            LoanApplication.status == ApplicationStatus.submitted,
        )
    ) or 0
    if pending >= settings.max_pending_applications:
        raise ApiError(409, "too_many_pending_applications",
                       "You already have applications under review",
                       details={"max": settings.max_pending_applications})

    app = LoanApplication(
        reference=next_application_ref(db),
        user_id=user.id,
        product_id=p.id,
        type=p.type,
        product_name_key=p.loan_name_key,
        amount=data.amount,
        term_months=data.term_months,
        annual_rate=p.annual_rate,
        monthly_payment=loan_math.round2(
            loan_math.annuity_payment(data.amount, p.annual_rate, data.term_months)),
        purpose=data.purpose,
        currency=settings.currency,
        status=ApplicationStatus.submitted,
    )
    db.add(app)
    db.commit()
    return get(db, app.id, user)


def get(db: Session, app_id: uuid.UUID, user: User | None = None) -> LoanApplication:
    stmt = select(LoanApplication).where(LoanApplication.id == app_id).options(
        selectinload(LoanApplication.loan), selectinload(LoanApplication.user))
    if user is not None:
        stmt = stmt.where(LoanApplication.user_id == user.id)
    a = db.scalar(stmt)
    if a is None:
        raise ApiError(404, "application_not_found", "Application not found")
    return a


def list_for(db: Session, user: User | None, status: ApplicationStatus | None,
             limit: int = 100, offset: int = 0) -> list[LoanApplication]:
    stmt = select(LoanApplication).options(
        selectinload(LoanApplication.loan), selectinload(LoanApplication.user))
    if user is not None:
        stmt = stmt.where(LoanApplication.user_id == user.id)
    if status is not None:
        stmt = stmt.where(LoanApplication.status == status)
    stmt = stmt.order_by(LoanApplication.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def _lock_submitted(db: Session, app_id: uuid.UUID, user: User | None = None) -> LoanApplication:
    stmt = select(LoanApplication).where(LoanApplication.id == app_id).with_for_update()
    if user is not None:
        stmt = stmt.where(LoanApplication.user_id == user.id)
    a = db.scalar(stmt)
    if a is None:
        raise ApiError(404, "application_not_found", "Application not found")
    if a.status != ApplicationStatus.submitted:
        raise ApiError(409, "application_not_pending",
                       f"Application is already {a.status.value}")
    return a


def cancel(db: Session, user: User, app_id: uuid.UUID) -> LoanApplication:
    a = _lock_submitted(db, app_id, user)
    a.status = ApplicationStatus.cancelled
    a.decided_at = now_utc()
    db.commit()
    return get(db, app_id)


def approve(db: Session, app_id: uuid.UUID, data: schemas.ApproveIn) -> tuple[LoanApplication, Loan]:
    a = _lock_submitted(db, app_id)
    user = db.get(User, a.user_id)
    product = db.get(LoanProduct, a.product_id)
    rate = data.annual_rate if data.annual_rate is not None else a.annual_rate
    loan = create_loan(
        db, user=user, product=product, amount=a.amount, annual_rate=rate,
        term_months=a.term_months, start_date=data.start_date or today(),
        product_name_key=a.product_name_key, application_id=a.id,
    )
    a.status = ApplicationStatus.approved
    a.decided_at = now_utc()
    a.decision_note = data.note
    if rate != a.annual_rate:
        a.annual_rate = rate
        a.monthly_payment = loan.installments[0].total
    db.commit()
    return get(db, app_id), loan


def reject(db: Session, app_id: uuid.UUID, reason: str) -> LoanApplication:
    a = _lock_submitted(db, app_id)
    a.status = ApplicationStatus.rejected
    a.decision_note = reason
    a.decided_at = now_utc()
    db.commit()
    return get(db, app_id)
