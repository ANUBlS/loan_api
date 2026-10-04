import hmac
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .errors import ApiError
from .models import AdminRole, AdminUser, User
from .security import decode_access_token, decode_admin_token

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


_ROLE_LEVEL = {AdminRole.viewer: 0, AdminRole.operator: 1, AdminRole.admin: 2}

# Used when a script calls the admin API with X-Admin-Key instead of a login.
API_KEY_ADMIN = AdminUser(
    id=None, username="api-key", full_name="X-Admin-Key", role=AdminRole.admin,
    password_hash="", is_active=True,
)


def get_current_admin(
    request: Request,
    db: DB,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_admin_key: Annotated[str | None, Header()] = None,
) -> AdminUser:
    if x_admin_key is not None:
        if not hmac.compare_digest(x_admin_key, settings.admin_api_key):
            raise ApiError(403, "forbidden", "Invalid admin key")
        return API_KEY_ADMIN
    if creds is None or creds.scheme.lower() != "bearer":
        raise ApiError(401, "unauthorized", "Sign in to the admin panel",
                       headers={"WWW-Authenticate": "Bearer"})
    admin_id, version = decode_admin_token(creds.credentials)
    admin = db.get(AdminUser, admin_id)
    if admin is None or not admin.is_active or admin.token_version != version:
        raise ApiError(401, "unauthorized", "Admin account disabled or session revoked")
    request.state.admin = admin
    return admin


CurrentAdmin = Annotated[AdminUser, Depends(get_current_admin)]


def require_role(minimum: AdminRole):
    def _check(admin: CurrentAdmin) -> AdminUser:
        if _ROLE_LEVEL[admin.role] < _ROLE_LEVEL[minimum]:
            raise ApiError(403, "forbidden", f"Requires role '{minimum.value}' or higher",
                           details={"required": minimum.value, "role": admin.role.value})
        return admin
    return _check


Viewer = Annotated[AdminUser, Depends(require_role(AdminRole.viewer))]
Operator = Annotated[AdminUser, Depends(require_role(AdminRole.operator))]
SuperAdmin = Annotated[AdminUser, Depends(require_role(AdminRole.admin))]


def require_admin(admin: Operator) -> AdminUser:
    """Kept for the original /admin/applications routes."""
    return admin
