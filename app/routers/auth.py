from fastapi import APIRouter, Response, status

from .. import schemas
from ..deps import DB, CurrentUser
from ..services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/otp/request", response_model=schemas.OtpRequestOut)
def request_code(data: schemas.OtpRequestIn, db: DB, language: str = "en"):
    """Step 1 of sign-in / registration: send a one-time code by SMS.

    `isRegistered=false` tells the app to ask for the full name before step 2.
    """
    return auth_service.request_otp(db, data.phone, language)


@router.post("/otp/verify", response_model=schemas.TokenOut)
def verify_code(data: schemas.OtpVerifyIn, db: DB):
    """Step 2: check the code and return tokens. Creates the user on first sign-in
    (`fullName` required then)."""
    return auth_service.verify_otp(db, data)


@router.post("/refresh", response_model=schemas.TokenOut)
def refresh(data: schemas.RefreshIn, db: DB):
    """Exchange a refresh token for a new pair. The old refresh token stops working."""
    return auth_service.refresh_tokens(db, data)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(data: schemas.LogoutIn, db: DB):
    """Sign out this device (revokes its refresh token)."""
    auth_service.logout(db, data.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(user: CurrentUser, db: DB):
    """Sign out every device of the current user."""
    auth_service.revoke_all(db, user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
