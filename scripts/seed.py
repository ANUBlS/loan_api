"""Seed the loan product catalogue and (optionally) a demo customer.

    python -m scripts.seed                       # products only
    python -m scripts.seed --demo                # + demo user +994501234567
    python -m scripts.seed --demo --phone "+994 55 555 55 55" --name "Test User"
    python -m scripts.seed --demo --reset        # recreate the demo user's loans

The demo loans mirror the app's lib/data/mock_data.dart and are dated relative
to today, so there is always one overdue, one active, one new and one closed loan.
"""

import argparse
from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import Loan, LoanApplication, LoanProduct, LoanType, Payment, User
from app.security import normalize_phone
from app.services.loan_math import add_months
from app.services.loans import create_loan, record_payment
from app.timeutils import _TZ, today

PRODUCTS = [
    # code, type, name key, loan name key, rate, min, max, step, min term, max term, active
    ("consumer", LoanType.consumer, "product.consumer", "product.consumer_loan", "17", "500", "30000", "100", 6, 60, True),
    ("car", LoanType.car, "product.car", "product.car_loan", "13.5", "5000", "80000", "500", 12, 84, True),
    ("mortgage", LoanType.mortgage, "product.mortgage", "product.mortgage_loan", "8", "10000", "300000", "1000", 36, 300, True),
    ("business", LoanType.business, "product.business", "product.business_loan", "15", "5000", "150000", "1000", 6, 84, True),
    # Existing loans only (not orderable in the app)
    ("express", LoanType.consumer, "product.express", "product.express_loan", "20", "300", "5000", "100", 3, 12, False),
]

# product code, loan name key, contract no, amount, rate, term, months ago, extra days back, paid count
DEMO_LOANS = [
    ("consumer", "product.consumer_loan", "CL-2025-004187", "5000", "18", 24, 10, 5, 10),
    ("car", "product.car_loan", "AU-2026-000932", "25000", "14", 48, 8, 10, 6),  # 2 overdue
    ("mortgage", "product.mortgage_loan", "MG-2026-000215", "80000", "9", 120, 3, 2, 3),
    ("express", "product.express_loan", "EX-2025-011508", "2000", "20", 12, 14, 0, 12),  # repaid
]


def seed_products(db) -> dict[str, LoanProduct]:
    out = {}
    for order, (code, typ, name, loan_name, rate, mn, mx, step, tmin, tmax, active) in enumerate(PRODUCTS):
        p = db.scalar(select(LoanProduct).where(LoanProduct.code == code)) or LoanProduct(code=code)
        p.type, p.name_key, p.loan_name_key = typ, name, loan_name
        p.annual_rate, p.min_amount, p.max_amount, p.step = map(Decimal, (rate, mn, mx, step))
        p.min_term, p.max_term, p.is_active, p.sort_order = tmin, tmax, active, order
        db.add(p)
        out[code] = p
    db.flush()
    print(f"Products: {len(out)} upserted")
    return out


def seed_demo(db, products, phone: str, name: str, reset: bool) -> None:
    phone = normalize_phone(phone)
    user = db.scalar(select(User).where(User.phone == phone))
    if user is None:
        user = User(phone=phone, full_name=name, language="az")
        db.add(user)
        db.flush()
    elif reset:
        loan_ids = select(Loan.id).where(Loan.user_id == user.id)
        db.execute(delete(Payment).where(Payment.loan_id.in_(loan_ids)))
        db.execute(delete(Loan).where(Loan.user_id == user.id))
        db.execute(delete(LoanApplication).where(LoanApplication.user_id == user.id))
        db.flush()
    elif db.scalar(select(Loan.id).where(Loan.user_id == user.id).limit(1)):
        print(f"Demo user {phone} already has loans (use --reset to recreate)")
        return

    t = today()
    for code, loan_name, contract, amount, rate, term, months, days, paid in DEMO_LOANS:
        start = add_months(t, -months) - timedelta(days=days)
        loan = create_loan(
            db, user=user, product=products[code], amount=Decimal(amount),
            annual_rate=Decimal(rate), term_months=term, start_date=start,
            product_name_key=loan_name, contract_no=contract,
        )
        for inst in loan.installments[:paid]:
            # Same as the app's mock: paid 0-2 days before the due date, at noon Baku time.
            day = inst.due_date - timedelta(days=inst.number % 3)
            record_payment(db, user, loan, inst,
                           paid_at=datetime.combine(day, time(12, 0), tzinfo=_TZ))
        db.flush()
    print(f"Demo user {phone} ({name}): {len(DEMO_LOANS)} loans created")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="create the demo customer")
    ap.add_argument("--phone", default="+994501234567")
    ap.add_argument("--name", default="Demo User")
    ap.add_argument("--reset", action="store_true", help="recreate the demo user's loans")
    args = ap.parse_args()

    with SessionLocal() as db:
        products = seed_products(db)
        if args.demo:
            seed_demo(db, products, args.phone, args.name, args.reset)
        db.commit()


if __name__ == "__main__":
    main()
