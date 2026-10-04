"""Admin panel accounts: login with lockout, password rules, user management."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import admin_schemas as A
from ..config import settings
from ..errors import ApiError
from ..models import AdminRole, AdminUser
from ..security import create_admin_token, hash_password, verify_password
from ..timeutils import now_utc
from .backoffice import audit

# Same cost as a real check, so a wrong username takes as long as a wrong password.
_DUMMY_HASH = hash_password("not-a-real-password")


def admin_out(a: AdminUser) -> A.AdminUserOut:
    return A.AdminUserOut.model_validate(a)


def check_password_policy(password: str, username: str | None = None) -> None:
    problems = []
    if len(password) < settings.admin_min_password_length:
        problems.append(f"at least {settings.admin_min_password_length} characters")
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        problems.append("letters and digits")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    if problems:
        raise ApiError(422, "password_weak", "Password needs: " + ", ".join(problems),
                       details={"rules": problems,
                                "minLength": settings.admin_min_password_length})


def login(db: Session, data: A.AdminLoginIn, ip: str | None) -> A.AdminTokenOut:
    admin = db.scalar(select(AdminUser).where(
        func.lower(AdminUser.username) == data.username.strip().lower()))
    now = now_utc()
    if admin is None:
        verify_password(data.password, _DUMMY_HASH)
        raise ApiError(401, "login_invalid", "Wrong username or password")
    if admin.locked_until and admin.locked_until > now:
        raise ApiError(429, "login_locked", "Too many failed attempts, try again later",
                       details={"lockedUntil": admin.locked_until.isoformat()})
    if not verify_password(data.password, admin.password_hash):
        admin.failed_logins += 1
        left = settings.admin_max_failed_logins - admin.failed_logins
        if left <= 0:
            admin.locked_until = now + timedelta(minutes=settings.admin_lock_minutes)
            admin.failed_logins = 0
            audit(db, admin, "admin.locked", "admin_user", admin.id, None, ip)
        db.commit()
        raise ApiError(401, "login_invalid", "Wrong username or password",
                       details={"attemptsLeft": max(left, 0)})
    if not admin.is_active:
        raise ApiError(403, "admin_disabled", "This admin account is disabled")
    admin.failed_logins = 0
    admin.locked_until = None
    admin.last_login_at = now
    audit(db, admin, "admin.login", "admin_user", admin.id, None, ip)
    db.commit()
    token, ttl = create_admin_token(admin.id, admin.role.value, admin.token_version)
    return A.AdminTokenOut(access_token=token, expires_in=ttl, admin=admin_out(admin))


def change_own_password(db: Session, admin: AdminUser, data: A.ChangePasswordIn,
                        ip) -> A.AdminTokenOut:
    if admin.id is None:
        raise ApiError(400, "not_supported", "Not available with X-Admin-Key")
    if not verify_password(data.current_password, admin.password_hash):
        raise ApiError(401, "login_invalid", "Current password is wrong")
    if data.new_password == data.current_password:
        raise ApiError(422, "password_same", "New password must be different")
    check_password_policy(data.new_password, admin.username)
    admin.password_hash = hash_password(data.new_password)
    admin.must_change_password = False
    admin.token_version += 1
    audit(db, admin, "admin.change_password", "admin_user", admin.id, None, ip)
    db.commit()
    token, ttl = create_admin_token(admin.id, admin.role.value, admin.token_version)
    return A.AdminTokenOut(access_token=token, expires_in=ttl, admin=admin_out(admin))


def list_admins(db: Session) -> list[A.AdminUserOut]:
    return [admin_out(a) for a in db.scalars(select(AdminUser).order_by(AdminUser.username))]


def _get(db: Session, admin_id: uuid.UUID) -> AdminUser:
    a = db.get(AdminUser, admin_id)
    if a is None:
        raise ApiError(404, "admin_not_found", "Admin user not found")
    return a


def create_admin(db: Session, actor: AdminUser | None, data: A.AdminUserCreateIn,
                 ip=None) -> A.AdminUserOut:
    if db.scalar(select(AdminUser).where(
            func.lower(AdminUser.username) == data.username.lower())):
        raise ApiError(409, "username_taken", "This username is already used")
    check_password_policy(data.password, data.username)
    a = AdminUser(
        username=data.username, full_name=data.full_name.strip(), role=data.role,
        password_hash=hash_password(data.password),
        must_change_password=data.must_change_password, is_active=True,
        failed_logins=0, token_version=0,
    )
    db.add(a)
    db.flush()
    if actor is not None:
        audit(db, actor, "admin.create", "admin_user", a.id,
              {"username": a.username, "role": a.role}, ip)
    db.commit()
    return admin_out(a)


def _active_superadmins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AdminUser).where(
        AdminUser.role == AdminRole.admin, AdminUser.is_active.is_(True))) or 0


def update_admin(db: Session, actor: AdminUser, admin_id: uuid.UUID,
                 data: A.AdminUserUpdateIn, ip) -> A.AdminUserOut:
    a = _get(db, admin_id)
    changes = data.model_dump(exclude_unset=True, by_alias=True)
    losing_admin = a.role == AdminRole.admin and a.is_active and (
        (data.role is not None and data.role != AdminRole.admin) or data.is_active is False)
    if losing_admin and _active_superadmins(db) <= 1:
        raise ApiError(409, "last_admin", "At least one active admin must remain")
    if data.full_name is not None:
        a.full_name = data.full_name.strip()
    if data.role is not None and data.role != a.role:
        a.role = data.role
        a.token_version += 1
    if data.is_active is not None and data.is_active != a.is_active:
        a.is_active = data.is_active
        a.token_version += 1
    audit(db, actor, "admin.update", "admin_user", a.id, changes, ip)
    db.commit()
    return admin_out(a)


def reset_admin_password(db: Session, actor: AdminUser, admin_id: uuid.UUID,
                         data: A.PasswordResetIn, ip) -> A.AdminUserOut:
    a = _get(db, admin_id)
    check_password_policy(data.new_password, a.username)
    a.password_hash = hash_password(data.new_password)
    a.must_change_password = data.must_change_password
    a.failed_logins = 0
    a.locked_until = None
    a.token_version += 1  # signs the admin out everywhere
    audit(db, actor, "admin.reset_password", "admin_user", a.id, None, ip)
    db.commit()
    return admin_out(a)
