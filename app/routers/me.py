from fastapi import APIRouter

from .. import schemas
from ..deps import DB, CurrentUser

router = APIRouter(prefix="/me", tags=["Profile"])


@router.get("", response_model=schemas.UserOut)
def get_me(user: CurrentUser):
    return user


@router.patch("", response_model=schemas.UserOut)
def update_me(data: schemas.UserUpdateIn, user: CurrentUser, db: DB):
    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.language is not None:
        user.language = data.language
    db.commit()
    db.refresh(user)
    return user
