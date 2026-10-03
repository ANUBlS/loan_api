"""Back-office endpoints (header X-Admin-Key). Approving an application is what
creates a real loan with its schedule and documents."""

import uuid

from fastapi import APIRouter, Depends, Query

from .. import schemas
from ..deps import DB, require_admin
from ..models import ApplicationStatus
from ..services import applications as app_service
from ..services import loans as loan_service

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)])


@router.get("/applications", response_model=list[schemas.AdminApplicationOut])
def list_applications(
    db: DB,
    status_: ApplicationStatus | None = Query(ApplicationStatus.submitted, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return [app_service.admin_application_out(a)
            for a in app_service.list_for(db, None, status_, limit, offset)]


@router.post("/applications/{application_id}/approve", response_model=schemas.LoanDetailOut)
def approve(application_id: uuid.UUID, data: schemas.ApproveIn, db: DB):
    """Approve → creates the loan (contract no, schedule, documents) and returns it."""
    _, loan = app_service.approve(db, application_id, data)
    loan = loan_service.get_user_loan(db, loan.user, loan.id)
    return loan_service.loan_detail(loan)


@router.post("/applications/{application_id}/reject", response_model=schemas.AdminApplicationOut)
def reject(application_id: uuid.UUID, data: schemas.RejectIn, db: DB):
    return app_service.admin_application_out(app_service.reject(db, application_id, data.reason))
