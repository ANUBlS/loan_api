import uuid

from fastapi import APIRouter, Query

from .. import schemas
from ..deps import DB, CurrentUser
from ..services import loans as loan_service

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.get("", response_model=schemas.PaymentPageOut)
def payment_history(
    user: CurrentUser,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    loan_id: uuid.UUID | None = Query(None, alias="loanId"),
):
    """Paid installments of all loans (or one loan), newest first."""
    return loan_service.payment_history(db, user, limit, offset, loan_id)
