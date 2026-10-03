from fastapi import APIRouter

from .. import schemas
from ..config import settings
from ..deps import DB
from ..services import applications as app_service
from ..services import loan_math
from ..timeutils import today

router = APIRouter(tags=["Catalog"])


@router.get("/catalog", response_model=schemas.CatalogOut)
def catalog(db: DB):
    """Loan products, purposes and currency for the "Order a loan" screen.
    Public: no token needed."""
    return schemas.CatalogOut(
        currency=settings.currency,
        products=[schemas.ProductOut.model_validate(p) for p in app_service.active_products(db)],
        purposes=app_service.PURPOSES,
    )


@router.post("/calculator", response_model=schemas.CalculatorOut)
def calculate(data: schemas.CalculatorIn, db: DB):
    """Monthly payment (and optionally the full schedule) for a product/amount/term.
    Uses the same rounding as real loans."""
    p = app_service.get_active_product(db, data.product_id)
    app_service.validate_terms(p, data.amount, data.term_months)
    q = loan_math.quote(data.amount, p.annual_rate, data.term_months, today())
    return schemas.CalculatorOut(
        product_id=p.id,
        amount=data.amount,
        term_months=data.term_months,
        annual_rate=p.annual_rate,
        currency=settings.currency,
        monthly_payment=q.monthly_payment,
        total_payment=q.total_payment,
        total_interest=q.total_interest,
        schedule=[
            schemas.ScheduleRowOut(
                number=r.number, due_date=r.due_date, principal=r.principal,
                interest=r.interest, total=r.total, balance_after=r.balance_after,
            )
            for r in q.schedule
        ] if data.include_schedule else None,
    )
