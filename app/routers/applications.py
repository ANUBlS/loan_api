import uuid

from fastapi import APIRouter, Query, status

from .. import schemas
from ..deps import DB, CurrentUser
from ..models import ApplicationStatus
from ..services import applications as app_service

router = APIRouter(prefix="/applications", tags=["Applications"])


@router.post("", response_model=schemas.ApplicationOut, status_code=status.HTTP_201_CREATED)
def submit_application(data: schemas.ApplicationIn, user: CurrentUser, db: DB):
    """Order a loan. Amount/term are checked against the product limits and step;
    the rate and monthly payment are set by the server.
    The app should call this only after confirmIdentity() (PIN / biometrics)."""
    return app_service.application_out(app_service.submit(db, user, data))


@router.get("", response_model=list[schemas.ApplicationOut])
def list_applications(
    user: CurrentUser,
    db: DB,
    status_: ApplicationStatus | None = Query(None, alias="status"),
):
    return [app_service.application_out(a) for a in app_service.list_for(db, user, status_)]


@router.get("/{application_id}", response_model=schemas.ApplicationOut)
def get_application(application_id: uuid.UUID, user: CurrentUser, db: DB):
    return app_service.application_out(app_service.get(db, application_id, user))


@router.post("/{application_id}/cancel", response_model=schemas.ApplicationOut)
def cancel_application(application_id: uuid.UUID, user: CurrentUser, db: DB):
    """Withdraw an application that is still under review."""
    return app_service.application_out(app_service.cancel(db, user, application_id))
