"""Admin panel logic: customers, loans, schedules, payments, documents, dashboard.

Rules that keep the data consistent with the mobile app:
- Installments are always paid in order (earliest unpaid first), so the paid
  installments of a loan are a prefix of its schedule.
- Only the latest live payment of a loan can be reversed (keeps that prefix).
- Money is Decimal end to end; the last installment absorbs rounding.
"""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .. import admin_schemas as A
from ..config import settings
from ..errors import ApiError
from ..models import (
    AdminUser,
    ApplicationStatus,
    AuditLog,
    Document,
    Installment,
    Loan,
    LoanApplication,
    LoanProduct,
    OtpCode,
    Payment,
    RefreshToken,
    User,
)
from ..security import normalize_phone
from ..timeutils import _TZ, now_utc, today
from . import documents as doc_service
from . import loan_math
from .applications import validate_terms
from .loans import ZERO, _summary_fields, installment_status, record_payment

# ------------------------------------------------------------------- audit


def audit(
    db: Session,
    admin: AdminUser,
    action: str,
    entity: str,
    entity_id=None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    db.add(AuditLog(
        admin_id=admin.id,
        admin_username=admin.username,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=_jsonable(details) if details else None,
        ip=ip,
    ))


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime, uuid.UUID)):
        return str(v)
    if hasattr(v, "value"):  # enums
        return v.value
    return v


# ---------------------------------------------------------------- helpers


def _unpaid_exists(on_overdue: bool, on: date):
    cond = [Installment.loan_id == Loan.id, Installment.paid_at.is_(None)]
    if on_overdue:
        cond.append(Installment.due_date < on)
    return exists().where(*cond)


def _state_filter(state: str | None, on: date):
    if state is None:
        return None
    unpaid, overdue = _unpaid_exists(False, on), _unpaid_exists(True, on)
    return {
        "open": unpaid,
        "overdue": overdue,
        "active": and_(unpaid, ~overdue),
        "closed": ~unpaid,
    }[state]


def _like(q: str) -> str:
    return "%" + q.strip().replace("%", r"\%").replace("_", r"\_") + "%"


# --------------------------------------------------------------- dashboard


def dashboard(db: Session) -> A.DashboardOut:
    on = today()
    unpaid, overdue = _unpaid_exists(False, on), _unpaid_exists(True, on)
    count = lambda *w: db.scalar(select(func.count()).select_from(Loan).where(*w)) or 0  # noqa: E731

    day_start = datetime.combine(on, time.min, tzinfo=_TZ)
    month_start = datetime.combine(on.replace(day=1), time.min, tzinfo=_TZ)
    live = Payment.reversed_at.is_(None)

    today_row = db.execute(
        select(func.count(), func.coalesce(func.sum(Payment.amount), 0))
        .where(live, Payment.paid_at >= day_start)
    ).one()
    month_amount = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(live, Payment.paid_at >= month_start)
    )

    first_month = loan_math.add_months(on.replace(day=1), -5)
    month_col = func.to_char(func.timezone(settings.timezone, Payment.paid_at), "YYYY-MM")
    rows = db.execute(
        select(month_col, func.count(), func.sum(Payment.amount))
        .where(live, Payment.paid_at >= datetime.combine(first_month, time.min, tzinfo=_TZ))
        .group_by(month_col)
    ).all()
    by_month = {m: (c, a) for m, c, a in rows}
    months = []
    for k in range(6):
        m = loan_math.add_months(first_month, k).strftime("%Y-%m")
        c, a = by_month.get(m, (0, ZERO))
        months.append(A.MonthTotal(month=m, count=c, amount=a))

    return A.DashboardOut(
        customers_total=db.scalar(select(func.count()).select_from(User)) or 0,
        customers_blocked=db.scalar(
            select(func.count()).select_from(User).where(User.is_active.is_(False))) or 0,
        loans_open=count(unpaid),
        loans_overdue=count(overdue),
        loans_closed=count(~unpaid),
        outstanding_principal=db.scalar(
            select(func.coalesce(func.sum(Installment.principal), 0))
            .where(Installment.paid_at.is_(None))),
        overdue_amount=db.scalar(
            select(func.coalesce(func.sum(Installment.principal + Installment.interest), 0))
            .where(Installment.paid_at.is_(None), Installment.due_date < on)),
        payments_today_count=today_row[0],
        payments_today_amount=today_row[1],
        payments_month_amount=month_amount,
        applications_pending=db.scalar(
            select(func.count()).select_from(LoanApplication)
            .where(LoanApplication.status == ApplicationStatus.submitted)) or 0,
        collections_by_month=months,
        currency=settings.currency,
    )


# ---------------------------------------------------------------- products


def product_out(p: LoanProduct) -> A.ProductAdminOut:
    return A.ProductAdminOut.model_validate(p)


def list_products(db: Session) -> list[A.ProductAdminOut]:
    return [product_out(p) for p in db.scalars(
        select(LoanProduct).order_by(LoanProduct.sort_order, LoanProduct.id))]


def _check_product_limits(p: LoanProduct) -> None:
    if p.min_amount > p.max_amount:
        raise ApiError(422, "product_invalid", "minAmount must be <= maxAmount")
    if p.min_term > p.max_term:
        raise ApiError(422, "product_invalid", "minTerm must be <= maxTerm")


def create_product(db: Session, admin: AdminUser, data: A.ProductCreateIn, ip) -> A.ProductAdminOut:
    if db.scalar(select(LoanProduct).where(LoanProduct.code == data.code)):
        raise ApiError(409, "product_code_taken", "A product with this code already exists")
    p = LoanProduct(**data.model_dump())
    _check_product_limits(p)
    db.add(p)
    db.flush()
    audit(db, admin, "product.create", "product", p.id, data.model_dump(), ip)
    db.commit()
    return product_out(p)


def update_product(db: Session, admin: AdminUser, product_id: int,
                   data: A.ProductUpdateIn, ip) -> A.ProductAdminOut:
    p = db.get(LoanProduct, product_id)
    if p is None:
        raise ApiError(404, "product_not_found", "Loan product not found")
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(p, k, v)
    _check_product_limits(p)
    audit(db, admin, "product.update", "product", p.id, changes, ip)
    db.commit()
    return product_out(p)


# --------------------------------------------------------------- customers


def _customer_stats(db: Session, users: list[User]) -> list[A.CustomerOut]:
    if not users:
        return []
    ids = [u.id for u in users]
    on = today()
    loans = db.scalars(
        select(Loan).where(Loan.user_id.in_(ids)).options(selectinload(Loan.installments))
    ).all()
    sessions = dict(db.execute(
        select(RefreshToken.user_id, func.count())
        .where(RefreshToken.user_id.in_(ids), RefreshToken.revoked_at.is_(None),
               RefreshToken.expires_at > now_utc())
        .group_by(RefreshToken.user_id)
    ).all())
    stats = {i: {"total": 0, "open": 0, "overdue": 0, "outstanding": ZERO} for i in ids}
    for loan in loans:
        st = stats[loan.user_id]
        st["total"] += 1
        unpaid = [i for i in loan.installments if not i.is_paid]
        if unpaid:
            st["open"] += 1
            st["outstanding"] += sum((i.principal for i in unpaid), ZERO)
            if any(i.due_date < on for i in unpaid):
                st["overdue"] += 1
    return [
        A.CustomerOut(
            id=u.id, phone=u.phone, full_name=u.full_name, language=u.language,
            is_active=u.is_active, created_at=u.created_at,
            loans_total=stats[u.id]["total"], loans_open=stats[u.id]["open"],
            loans_overdue=stats[u.id]["overdue"],
            outstanding_principal=stats[u.id]["outstanding"],
            active_sessions=sessions.get(u.id, 0),
        )
        for u in users
    ]


def list_customers(db: Session, q: str | None, status: str | None,
                   limit: int, offset: int) -> A.Page[A.CustomerOut]:
    where = []
    if q and q.strip():
        digits = "".join(ch for ch in q if ch.isdigit())
        cond = [User.full_name.ilike(_like(q))]
        if digits:
            cond.append(User.phone.like(_like(digits)))
        where.append(or_(*cond))
    if status == "active":
        where.append(User.is_active.is_(True))
    elif status == "blocked":
        where.append(User.is_active.is_(False))
    total = db.scalar(select(func.count()).select_from(User).where(*where)) or 0
    users = db.scalars(
        select(User).where(*where).order_by(User.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return A.Page[A.CustomerOut](items=_customer_stats(db, list(users)),
                                 total=total, limit=limit, offset=offset)


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "customer_not_found", "Customer not found")
    return user


def customer_out(db: Session, user: User) -> A.CustomerOut:
    return _customer_stats(db, [user])[0]


def _phone_free(db: Session, phone: str, except_id: uuid.UUID | None = None) -> None:
    other = db.scalar(select(User).where(User.phone == phone))
    if other is not None and other.id != except_id:
        raise ApiError(409, "phone_taken", "Another customer already uses this phone number")


def create_customer(db: Session, admin: AdminUser, data: A.CustomerCreateIn, ip) -> A.CustomerOut:
    phone = normalize_phone(data.phone)
    _phone_free(db, phone)
    user = User(phone=phone, full_name=data.full_name.strip(), language=data.language)
    db.add(user)
    db.flush()
    audit(db, admin, "customer.create", "customer", user.id,
          {"phone": phone, "fullName": user.full_name}, ip)
    db.commit()
    return customer_out(db, user)


def update_customer(db: Session, admin: AdminUser, user_id: uuid.UUID,
                    data: A.CustomerUpdateIn, ip) -> A.CustomerOut:
    user = get_user(db, user_id)
    changes = data.model_dump(exclude_unset=True, by_alias=True)
    if data.phone is not None:
        phone = normalize_phone(data.phone)
        _phone_free(db, phone, user.id)
        if phone != user.phone:
            user.phone = phone
            # The phone is the login: sign out everywhere when it changes.
            _revoke_sessions(db, user.id)
        changes["phone"] = phone
    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.language is not None:
        user.language = data.language
    audit(db, admin, "customer.update", "customer", user.id, changes, ip)
    db.commit()
    return customer_out(db, user)


def _revoke_sessions(db: Session, user_id: uuid.UUID) -> int:
    rows = db.scalars(
        select(RefreshToken).where(RefreshToken.user_id == user_id,
                                   RefreshToken.revoked_at.is_(None))
    ).all()
    now = now_utc()
    for rt in rows:
        rt.revoked_at = now
    return len(rows)


def set_customer_active(db: Session, admin: AdminUser, user_id: uuid.UUID,
                        active: bool, ip) -> A.CustomerOut:
    user = get_user(db, user_id)
    user.is_active = active
    revoked = 0 if active else _revoke_sessions(db, user.id)
    audit(db, admin, "customer.unblock" if active else "customer.block", "customer", user.id,
          {"sessionsRevoked": revoked}, ip)
    db.commit()
    return customer_out(db, user)


def reset_access(db: Session, admin: AdminUser, user_id: uuid.UUID, ip) -> A.ResetAccessOut:
    """Customers have no server password: the phone + SMS code is the login and the
    PIN lives on the phone. Reset = sign out of every device and clear SMS-code
    limits, so the next login asks for a fresh SMS code and a new PIN."""
    user = get_user(db, user_id)
    revoked = _revoke_sessions(db, user.id)
    cleared = db.execute(delete(OtpCode).where(OtpCode.phone == user.phone)).rowcount or 0
    audit(db, admin, "customer.reset_access", "customer", user.id,
          {"sessionsRevoked": revoked, "otpCleared": cleared}, ip)
    db.commit()
    return A.ResetAccessOut(sessions_revoked=revoked, otp_codes_cleared=cleared)


# ------------------------------------------------------------------- loans


def _live_payments(db: Session, loan_ids: list[uuid.UUID]) -> dict[int, Payment]:
    if not loan_ids:
        return {}
    rows = db.scalars(select(Payment).where(
        Payment.loan_id.in_(loan_ids), Payment.reversed_at.is_(None))).all()
    return {p.installment_id: p for p in rows}


def admin_loan_out(loan: Loan, on: date | None = None) -> A.AdminLoanOut:
    return A.AdminLoanOut(
        **_summary_fields(loan, on or today()),
        user_id=loan.user_id, user_phone=loan.user.phone, user_full_name=loan.user.full_name,
        product_id=loan.product_id, application_id=loan.application_id,
        created_at=loan.created_at,
    )


def admin_loan_detail(db: Session, loan: Loan) -> A.AdminLoanDetailOut:
    on = today()
    base = admin_loan_out(loan, on)
    next_no = base.next_installment.number if base.next_installment else None
    pays = _live_payments(db, [loan.id])
    schedule = []
    for i in loan.installments:
        p = pays.get(i.id)
        schedule.append(A.AdminInstallmentOut(
            id=i.id, number=i.number, due_date=i.due_date, principal=i.principal,
            interest=i.interest, total=i.total, balance_after=i.balance_after,
            paid_date=i.paid_at, status=installment_status(i, next_no, on),
            payment_id=p.id if p else None, payment_reference=p.reference if p else None,
        ))
    diff = loan.amount - sum((i.principal for i in loan.installments), ZERO)
    return A.AdminLoanDetailOut(**base.model_dump(), schedule=schedule, principal_difference=diff)


def _load_loan(db: Session, loan_id: uuid.UUID, *, lock: bool = False) -> Loan:
    stmt = select(Loan).where(Loan.id == loan_id)
    if lock:
        stmt = stmt.with_for_update()
    loan = db.scalar(stmt)
    if loan is None:
        raise ApiError(404, "loan_not_found", "Loan not found")
    if lock:
        db.refresh(loan, ["installments"])
    return loan


def get_loan_detail(db: Session, loan_id: uuid.UUID) -> A.AdminLoanDetailOut:
    db.expire_all()
    loan = db.scalar(
        select(Loan).where(Loan.id == loan_id)
        .options(selectinload(Loan.installments), selectinload(Loan.user))
    )
    if loan is None:
        raise ApiError(404, "loan_not_found", "Loan not found")
    return admin_loan_detail(db, loan)


def list_loans(db: Session, q: str | None, state: str | None, user_id: uuid.UUID | None,
               limit: int, offset: int) -> A.Page[A.AdminLoanOut]:
    on = today()
    where = []
    if user_id:
        where.append(Loan.user_id == user_id)
    if (f := _state_filter(state, on)) is not None:
        where.append(f)
    stmt = select(Loan).join(User, User.id == Loan.user_id)
    if q and q.strip():
        digits = "".join(ch for ch in q if ch.isdigit())
        cond = [Loan.contract_no.ilike(_like(q)), User.full_name.ilike(_like(q))]
        if len(digits) >= 3:
            cond.append(User.phone.like(_like(digits)))
        where.append(or_(*cond))
    total = db.scalar(
        select(func.count()).select_from(Loan).join(User, User.id == Loan.user_id).where(*where)
    ) or 0
    loans = db.scalars(
        stmt.where(*where)
        .options(selectinload(Loan.installments), selectinload(Loan.user))
        .order_by(Loan.created_at.desc(), Loan.contract_no.desc())
        .limit(limit).offset(offset)
    ).all()
    return A.Page[A.AdminLoanOut](items=[admin_loan_out(l, on) for l in loans],
                                  total=total, limit=limit, offset=offset)


def _get_product(db: Session, product_id: int) -> LoanProduct:
    p = db.get(LoanProduct, product_id)
    if p is None:
        raise ApiError(404, "product_not_found", "Loan product not found")
    return p


def _contract_free(db: Session, contract_no: str, except_id: uuid.UUID | None = None) -> None:
    other = db.scalar(select(Loan).where(Loan.contract_no == contract_no))
    if other is not None and other.id != except_id:
        raise ApiError(409, "contract_no_taken", "Another loan already has this contract number")


def create_loan(db: Session, admin: AdminUser, data: A.LoanCreateIn, ip) -> A.AdminLoanDetailOut:
    from .loans import create_loan as _create

    user = get_user(db, data.user_id)
    if not user.is_active:
        raise ApiError(409, "customer_blocked", "Customer is blocked")
    product = _get_product(db, data.product_id)
    if data.check_product_limits:
        validate_terms(product, data.amount, data.term_months)
    if data.contract_no:
        _contract_free(db, data.contract_no)
    loan = _create(
        db, user=user, product=product, amount=data.amount,
        annual_rate=data.annual_rate if data.annual_rate is not None else product.annual_rate,
        term_months=data.term_months, start_date=data.start_date or today(),
        contract_no=data.contract_no,
    )
    audit(db, admin, "loan.create", "loan", loan.id,
          {**data.model_dump(by_alias=True), "contractNo": loan.contract_no}, ip)
    db.commit()
    return get_loan_detail(db, loan.id)


def _has_live_payments(db: Session, loan_id: uuid.UUID) -> bool:
    return bool(db.scalar(select(func.count()).select_from(Payment).where(
        Payment.loan_id == loan_id, Payment.reversed_at.is_(None))))


def _rebuild_schedule(db: Session, loan: Loan) -> None:
    # Delete first (and flush): the unit of work would otherwise insert the new
    # rows before deleting the old ones and hit the (loan_id, number) unique key.
    db.execute(delete(Installment).where(Installment.loan_id == loan.id))
    db.flush()
    db.expire(loan, ["installments"])
    for row in loan_math.build_schedule(loan.amount, loan.annual_rate,
                                        loan.term_months, loan.start_date):
        loan.installments.append(Installment(
            number=row.number, due_date=row.due_date, principal=row.principal,
            interest=row.interest, balance_after=row.balance_after,
        ))


def update_loan(db: Session, admin: AdminUser, loan_id: uuid.UUID,
                data: A.LoanUpdateIn, ip) -> A.AdminLoanDetailOut:
    loan = _load_loan(db, loan_id, lock=True)
    changes = data.model_dump(exclude_unset=True, by_alias=True)
    if data.contract_no is not None and data.contract_no != loan.contract_no:
        _contract_free(db, data.contract_no, loan.id)
        loan.contract_no = data.contract_no

    financial = {k: v for k, v in data.model_dump(exclude_unset=True).items()
                 if k in ("product_id", "amount", "annual_rate", "term_months", "start_date")
                 and v is not None}
    if financial:
        if _has_live_payments(db, loan.id):
            raise ApiError(409, "loan_has_payments",
                           "Amount, rate, term, start date and product can only change "
                           "while the loan has no payments. Use restructure instead.")
        if "product_id" in financial:
            product = _get_product(db, financial["product_id"])
            loan.product_id = product.id
            loan.type = product.type
            loan.product_name_key = product.loan_name_key
        for k in ("amount", "annual_rate", "term_months", "start_date"):
            if k in financial:
                setattr(loan, k, loan_math.round2(financial[k]) if k == "amount" else financial[k])
        # Installments may have reversed payments pointing at them; drop those first.
        db.execute(delete(Payment).where(Payment.loan_id == loan.id))
        _rebuild_schedule(db, loan)
        db.flush()

    if financial or "contract_no" in data.model_fields_set:
        db.refresh(loan, ["installments"])
        doc_service.regenerate(db, loan, loan.user)
    audit(db, admin, "loan.update", "loan", loan.id, changes, ip)
    db.commit()
    return get_loan_detail(db, loan.id)


def delete_loan(db: Session, admin: AdminUser, loan_id: uuid.UUID, ip) -> None:
    loan = _load_loan(db, loan_id, lock=True)
    if _has_live_payments(db, loan.id):
        raise ApiError(409, "loan_has_payments",
                       "Reverse all payments before deleting the loan")
    info = {"contractNo": loan.contract_no, "userId": loan.user_id, "amount": loan.amount}
    db.execute(delete(Payment).where(Payment.loan_id == loan.id))
    db.delete(loan)
    audit(db, admin, "loan.delete", "loan", loan_id, info, ip)
    db.commit()


def _recompute_balances(loan: Loan) -> None:
    balance = loan.amount
    for i in sorted(loan.installments, key=lambda x: x.number):
        balance = loan_math.round2(balance - i.principal)
        i.balance_after = max(balance, ZERO)


def update_installment(db: Session, admin: AdminUser, loan_id: uuid.UUID, number: int,
                       data: A.InstallmentUpdateIn, ip) -> A.AdminLoanDetailOut:
    loan = _load_loan(db, loan_id, lock=True)
    inst = next((i for i in loan.installments if i.number == number), None)
    if inst is None:
        raise ApiError(404, "installment_not_found", "Installment not found")
    if inst.is_paid:
        raise ApiError(409, "installment_paid", "A paid installment cannot be edited")
    before = {"dueDate": inst.due_date, "principal": inst.principal, "interest": inst.interest}
    if data.due_date is not None:
        inst.due_date = data.due_date
    if data.principal is not None:
        inst.principal = loan_math.round2(data.principal)
    if data.interest is not None:
        inst.interest = loan_math.round2(data.interest)
    _recompute_balances(loan)
    db.flush()
    doc_service.regenerate(db, loan, loan.user, {"doc.schedule"})
    audit(db, admin, "loan.installment.update", "loan", loan.id,
          {"number": number, "before": before,
           "after": data.model_dump(exclude_unset=True, by_alias=True)}, ip)
    db.commit()
    return get_loan_detail(db, loan.id)


def restructure(db: Session, admin: AdminUser, loan_id: uuid.UUID,
                data: A.RestructureIn, ip) -> A.AdminLoanDetailOut:
    loan = _load_loan(db, loan_id, lock=True)
    sched = sorted(loan.installments, key=lambda x: x.number)
    paid = [i for i in sched if i.is_paid]
    unpaid = [i for i in sched if not i.is_paid]
    if not unpaid:
        raise ApiError(409, "loan_closed", "Loan is fully repaid")
    # True outstanding = amount - principal already repaid. This also absorbs any
    # difference left by hand-edited installments.
    outstanding = loan_math.round2(loan.amount - sum((i.principal for i in paid), ZERO))
    if outstanding <= 0:
        raise ApiError(409, "nothing_outstanding", "No outstanding principal to restructure")
    n = data.term_months or len(unpaid)
    rate = data.annual_rate if data.annual_rate is not None else loan.annual_rate
    first_due = data.first_due_date or unpaid[0].due_date
    rows = loan_math.build_schedule(outstanding, rate, n, loan_math.add_months(first_due, -1))

    # Reversed payments can point at unpaid installments; remove before replacing them.
    db.execute(delete(Payment).where(
        Payment.installment_id.in_([i.id for i in unpaid])))
    for i in unpaid:
        loan.installments.remove(i)
    db.flush()
    start_no = len(paid)
    for row in rows:
        loan.installments.append(Installment(
            number=start_no + row.number, due_date=row.due_date, principal=row.principal,
            interest=row.interest, balance_after=row.balance_after,
        ))
    loan.term_months = start_no + n
    loan.annual_rate = Decimal(rate)
    db.flush()
    db.refresh(loan, ["installments"])
    _recompute_balances(loan)
    db.flush()
    doc_service.regenerate(db, loan, loan.user, {"doc.schedule"})
    audit(db, admin, "loan.restructure", "loan", loan.id,
          {"outstanding": outstanding, "installments": n, "annualRate": rate,
           "firstDueDate": first_due, "replaced": len(unpaid)}, ip)
    db.commit()
    return get_loan_detail(db, loan.id)


def regenerate_documents(db: Session, admin: AdminUser, loan_id: uuid.UUID, ip) -> None:
    loan = _load_loan(db, loan_id, lock=True)
    doc_service.regenerate(db, loan, loan.user)
    audit(db, admin, "loan.documents.regenerate", "loan", loan.id, None, ip)
    db.commit()


# ---------------------------------------------------------------- payments


def admin_payment_out(p: Payment, admins: dict[uuid.UUID, str]) -> A.AdminPaymentOut:
    return A.AdminPaymentOut(
        id=p.id, reference=p.reference, loan_id=p.loan_id, contract_no=p.loan.contract_no,
        user_id=p.user_id, user_phone=p.loan.user.phone, user_full_name=p.loan.user.full_name,
        installment_number=p.installment.number, amount=p.amount, currency=p.currency,
        paid_at=p.paid_at, method=p.method, note=p.note,
        created_by=admins.get(p.created_by_admin_id),
        reversed_at=p.reversed_at, reversed_by=admins.get(p.reversed_by_admin_id),
        reversal_reason=p.reversal_reason, created_at=p.created_at,
    )


def _admin_names(db: Session, payments: list[Payment]) -> dict[uuid.UUID, str]:
    ids = {x for p in payments for x in (p.created_by_admin_id, p.reversed_by_admin_id) if x}
    if not ids:
        return {}
    return dict(db.execute(select(AdminUser.id, AdminUser.username)
                           .where(AdminUser.id.in_(ids))).all())


def _payment_options():
    return (selectinload(Payment.loan).selectinload(Loan.user), selectinload(Payment.installment))


def list_payments(db: Session, q: str | None, loan_id: uuid.UUID | None,
                  user_id: uuid.UUID | None, method: str | None, date_from: date | None,
                  date_to: date | None, include_reversed: bool,
                  limit: int, offset: int) -> A.Page[A.AdminPaymentOut]:
    where = []
    if not include_reversed:
        where.append(Payment.reversed_at.is_(None))
    if loan_id:
        where.append(Payment.loan_id == loan_id)
    if user_id:
        where.append(Payment.user_id == user_id)
    if method:
        where.append(Payment.method == method)
    if date_from:
        where.append(Payment.paid_at >= datetime.combine(date_from, time.min, tzinfo=_TZ))
    if date_to:
        where.append(Payment.paid_at < datetime.combine(date_to + timedelta(days=1),
                                                        time.min, tzinfo=_TZ))
    base = select(Payment).join(Loan, Loan.id == Payment.loan_id).join(User, User.id == Payment.user_id)
    if q and q.strip():
        digits = "".join(ch for ch in q if ch.isdigit())
        cond = [Payment.reference.ilike(_like(q)), Loan.contract_no.ilike(_like(q)),
                User.full_name.ilike(_like(q))]
        if len(digits) >= 3:
            cond.append(User.phone.like(_like(digits)))
        where.append(or_(*cond))
    total = db.scalar(select(func.count()).select_from(base.where(*where).subquery())) or 0
    rows = db.scalars(
        base.where(*where).options(*_payment_options())
        .order_by(Payment.paid_at.desc(), Payment.reference.desc())
        .limit(limit).offset(offset)
    ).all()
    names = _admin_names(db, list(rows))
    return A.Page[A.AdminPaymentOut](items=[admin_payment_out(p, names) for p in rows],
                                     total=total, limit=limit, offset=offset)


def _get_payment(db: Session, payment_id: uuid.UUID) -> Payment:
    p = db.scalar(select(Payment).where(Payment.id == payment_id).options(*_payment_options()))
    if p is None:
        raise ApiError(404, "payment_not_found", "Payment not found")
    return p


def create_payments(db: Session, admin: AdminUser, data: A.PaymentCreateIn,
                    ip) -> list[A.AdminPaymentOut]:
    loan = _load_loan(db, data.loan_id, lock=True)
    unpaid = db.scalars(
        select(Installment)
        .where(Installment.loan_id == loan.id, Installment.paid_at.is_(None))
        .order_by(Installment.number).limit(data.installments).with_for_update()
    ).all()
    if not unpaid:
        raise ApiError(409, "loan_closed", "Loan is fully repaid")
    if len(unpaid) < data.installments:
        raise ApiError(422, "too_many_installments",
                       f"Only {len(unpaid)} unpaid installment(s) left",
                       details={"unpaid": len(unpaid)})
    paid_at = data.paid_at or now_utc()
    if paid_at.tzinfo is None:
        paid_at = paid_at.replace(tzinfo=_TZ)
    created = [record_payment(db, loan.user, loan, inst, paid_at=paid_at,
                              method=data.method, note=data.note,
                              admin_id=admin.id) for inst in unpaid]
    db.flush()
    audit(db, admin, "payment.create", "loan", loan.id,
          {"references": [p.reference for p in created], "method": data.method,
           "amount": sum((p.amount for p in created), ZERO), "paidAt": paid_at}, ip)
    db.commit()
    rows = [_get_payment(db, p.id) for p in created]
    names = _admin_names(db, rows)
    return [admin_payment_out(p, names) for p in rows]


def reverse_payment(db: Session, admin: AdminUser, payment_id: uuid.UUID,
                    reason: str, ip) -> A.AdminPaymentOut:
    p = _get_payment(db, payment_id)
    if p.reversed_at is not None:
        raise ApiError(409, "payment_reversed", "Payment is already reversed")
    _load_loan(db, p.loan_id, lock=True)
    latest = db.scalar(
        select(Payment).join(Installment, Installment.id == Payment.installment_id)
        .where(Payment.loan_id == p.loan_id, Payment.reversed_at.is_(None))
        .order_by(Installment.number.desc()).limit(1)
    )
    if latest is None or latest.id != p.id:
        raise ApiError(409, "payment_not_last",
                       "Only the latest payment of a loan can be reversed; "
                       "reverse the later payments first",
                       details={"latestReference": latest.reference if latest else None})
    p.reversed_at = now_utc()
    p.reversed_by_admin_id = admin.id
    p.reversal_reason = reason
    p.installment.paid_at = None
    audit(db, admin, "payment.reverse", "payment", p.id,
          {"reference": p.reference, "amount": p.amount, "reason": reason}, ip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    p = _get_payment(db, payment_id)
    return admin_payment_out(p, _admin_names(db, [p]))


# --------------------------------------------------------------- documents


ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _sniff_type(content: bytes, declared: str | None) -> str:
    if content.startswith(b"%PDF"):
        return "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"\xd0\xcf\x11\xe0"):
        return "application/msword"
    if content.startswith(b"PK\x03\x04") and (declared or "").endswith("wordprocessingml.document"):
        return declared
    raise ApiError(415, "unsupported_file_type",
                   "Allowed: PDF, JPG, PNG, WEBP, DOC, DOCX",
                   details={"allowed": sorted(ALLOWED_TYPES)})


def _safe_name(name: str | None, content_type: str, fallback: str) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1].strip()
    base = "".join(ch for ch in base if ch.isalnum() or ch in "._- ()")[:110]
    if not base:
        base = fallback + ALLOWED_TYPES[content_type]
    return base


def _check_size(content: bytes) -> None:
    if not content:
        raise ApiError(422, "file_empty", "The file is empty")
    limit = settings.max_upload_mb * 1024 * 1024
    if len(content) > limit:
        raise ApiError(413, "file_too_large", f"Maximum file size is {settings.max_upload_mb} MB",
                       details={"maxMb": settings.max_upload_mb})


def admin_document_out(d: Document) -> A.AdminDocumentOut:
    return A.AdminDocumentOut.model_validate(d)


def list_documents(db: Session, loan_id: uuid.UUID) -> list[A.AdminDocumentOut]:
    _load_loan(db, loan_id)
    return [admin_document_out(d) for d in db.scalars(
        select(Document).where(Document.loan_id == loan_id)
        .order_by(Document.sort_order, Document.created_at))]


def get_document(db: Session, doc_id: uuid.UUID) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise ApiError(404, "document_not_found", "Document not found")
    return doc


def upload_document(db: Session, admin: AdminUser, loan_id: uuid.UUID, name_key: str,
                    file_name: str | None, declared_type: str | None, content: bytes,
                    ip) -> A.AdminDocumentOut:
    loan = _load_loan(db, loan_id)
    _check_size(content)
    ctype = _sniff_type(content, declared_type)
    order = (db.scalar(select(func.max(Document.sort_order))
                       .where(Document.loan_id == loan.id)) or 0) + 1
    doc = Document(
        loan_id=loan.id, name_key=name_key,
        file_name=_safe_name(file_name, ctype, f"{loan.contract_no}-document"),
        content_type=ctype, size_bytes=len(content), content=content, sort_order=order,
    )
    db.add(doc)
    db.flush()
    audit(db, admin, "document.upload", "document", doc.id,
          {"loanId": loan.id, "nameKey": name_key, "fileName": doc.file_name,
           "sizeBytes": len(content)}, ip)
    db.commit()
    return admin_document_out(doc)


def replace_document_file(db: Session, admin: AdminUser, doc_id: uuid.UUID,
                          file_name: str | None, declared_type: str | None,
                          content: bytes, ip) -> A.AdminDocumentOut:
    doc = get_document(db, doc_id)
    _check_size(content)
    ctype = _sniff_type(content, declared_type)
    doc.content = content
    doc.content_type = ctype
    doc.size_bytes = len(content)
    if file_name:
        doc.file_name = _safe_name(file_name, ctype, doc.file_name)
    audit(db, admin, "document.replace", "document", doc.id,
          {"fileName": doc.file_name, "sizeBytes": len(content)}, ip)
    db.commit()
    return admin_document_out(doc)


def update_document(db: Session, admin: AdminUser, doc_id: uuid.UUID,
                    data: A.DocumentUpdateIn, ip) -> A.AdminDocumentOut:
    doc = get_document(db, doc_id)
    changes = data.model_dump(exclude_unset=True, by_alias=True)
    if data.name_key is not None:
        doc.name_key = data.name_key
    if data.file_name is not None:
        doc.file_name = _safe_name(data.file_name, doc.content_type, doc.file_name)
    if data.sort_order is not None:
        doc.sort_order = data.sort_order
    audit(db, admin, "document.update", "document", doc.id, changes, ip)
    db.commit()
    return admin_document_out(doc)


def delete_document(db: Session, admin: AdminUser, doc_id: uuid.UUID, ip) -> None:
    doc = get_document(db, doc_id)
    info = {"loanId": doc.loan_id, "nameKey": doc.name_key, "fileName": doc.file_name}
    db.delete(doc)
    audit(db, admin, "document.delete", "document", doc_id, info, ip)
    db.commit()


# ------------------------------------------------------------------- audit


def list_audit(db: Session, entity: str | None, entity_id: str | None,
               admin_id: uuid.UUID | None, action: str | None,
               limit: int, offset: int) -> A.Page[A.AuditOut]:
    where = []
    if entity:
        where.append(AuditLog.entity == entity)
    if entity_id:
        where.append(AuditLog.entity_id == entity_id)
    if admin_id:
        where.append(AuditLog.admin_id == admin_id)
    if action:
        where.append(AuditLog.action.like(action.replace("*", "%")))
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*where)) or 0
    rows = db.scalars(select(AuditLog).where(*where)
                      .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                      .limit(limit).offset(offset)).all()
    return A.Page[A.AuditOut](items=[A.AuditOut.model_validate(r) for r in rows],
                              total=total, limit=limit, offset=offset)
