import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from .. import schemas
from ..deps import DB, CurrentUser
from ..services import documents as doc_service
from ..services import loans as loan_service

router = APIRouter(prefix="/loans", tags=["Loans"])


@router.get("", response_model=schemas.LoanListOut)
def list_loans(user: CurrentUser, db: DB):
    """All loans of the user, sorted overdue → active → closed."""
    return loan_service.list_loans(db, user)


@router.get("/{loan_id}", response_model=schemas.LoanDetailOut)
def get_loan(loan_id: uuid.UUID, user: CurrentUser, db: DB):
    """One loan with its full payment schedule."""
    return loan_service.loan_detail(loan_service.get_user_loan(db, user, loan_id))


@router.get("/{loan_id}/schedule", response_model=list[schemas.InstallmentOut])
def get_schedule(loan_id: uuid.UUID, user: CurrentUser, db: DB):
    return loan_service.loan_detail(loan_service.get_user_loan(db, user, loan_id)).schedule


@router.post(
    "/{loan_id}/payments",
    response_model=schemas.PayNextOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Repeated request with the same Idempotency-Key"}},
)
def pay_next_installment(
    loan_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=80)] = None,
):
    """Pays the earliest unpaid installment (overdue first) — mock payment, no card.

    Send a unique `Idempotency-Key` header (e.g. a UUID) per tap: if the network
    drops and the app retries, the installment is not paid twice.
    The app should call this only after confirmIdentity() (PIN / biometrics).
    """
    result, created = loan_service.pay_next(db, user, loan_id, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("/{loan_id}/documents", response_model=list[schemas.DocumentOut])
def list_documents(loan_id: uuid.UUID, user: CurrentUser, db: DB):
    loan_service.get_user_loan(db, user, loan_id)
    return doc_service.list_for_loan(db, loan_id)
