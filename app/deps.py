import hmac
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .errors import ApiError
from .models import User
from .security import decode_access_token

_bearer = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DB,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise ApiError(401, "unauthorized", "Missing bearer token",
                       headers={"WWW-Authenticate": "Bearer"})
    user_id = decode_access_token(creds.credentials)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "unauthorized", "User not found or disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise ApiError(403, "forbidden", "Invalid admin key")
