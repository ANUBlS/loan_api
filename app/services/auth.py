"""Phone + one-time SMS code sign-in, JWT access tokens, rotating refresh tokens.

The 6-digit passcode and biometrics stay on the device: they unlock the app
locally. The server only trusts the phone number proven by the SMS code.
"""

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import schemas
from ..config import settings
from ..errors import ApiError
from ..models import OtpCode, RefreshToken, User
from ..security import (
    create_access_token,
    generate_otp,
    generate_refresh_token,
    hash_otp,
    normalize_phone,
    otp_matches,
    sha256_hex,
)
from ..timeutils import now_utc
from .sms import get_sender

_OTP_TEXT = {
    "en": "Your Loan App code: {code}. Do not share it with anyone.",
    "az": "Kredit Tətbiqi kodunuz: {code}. Kodu heç kimə deməyin.",
    "ru": "Ваш код Loan App: {code}. Никому его не сообщайте.",
}


# ----------------------------------------------------------------------- OTP


def request_otp(db: Session, raw_phone: str, language: str = "en") -> schemas.OtpRequestOut:
    phone = normalize_phone(raw_phone)
    now = now_utc()

    last = db.scalar(
        select(OtpCode).where(OtpCode.phone == phone).order_by(OtpCode.created_at.desc()).limit(1)
    )
    if last is not None:
        wait = settings.otp_resend_seconds - int((now - last.created_at).total_seconds())
        if wait > 0:
            raise ApiError(429, "otp_too_soon", "Wait before requesting a new code",
                           details={"retryAfter": wait}, headers={"Retry-After": str(wait)})

    sent_last_hour = db.scalar(
        select(func.count()).select_from(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.created_at > now - timedelta(hours=1))
    ) or 0
    if sent_last_hour >= settings.otp_max_per_hour:
        raise ApiError(429, "otp_rate_limited", "Too many codes requested, try later",
                       headers={"Retry-After": "3600"})

    # Only the newest code is valid.
    db.execute(
        update(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = generate_otp()
    db.add(OtpCode(
        phone=phone,
        code_hash=hash_otp(phone, code),
        expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
    ))
    db.commit()

    text = _OTP_TEXT.get(language, _OTP_TEXT["en"]).format(code=code)
    get_sender().send(phone, text)

    registered = db.scalar(select(User.id).where(User.phone == phone)) is not None
    return schemas.OtpRequestOut(
        phone=phone,
        expires_in=settings.otp_ttl_seconds,
        resend_in=settings.otp_resend_seconds,
        is_registered=registered,
        debug_code=code if settings.expose_otp_in_response else None,
    )


def _consume_otp(db: Session, phone: str, code: str) -> None:
    now = now_utc()
    otp = db.scalar(
        select(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.consumed_at.is_(None))
        .order_by(OtpCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if otp is None or otp.expires_at <= now:
        raise ApiError(400, "otp_expired", "Code expired, request a new one")
    if otp.attempts >= settings.otp_max_attempts:
        raise ApiError(400, "otp_attempts_exceeded", "Too many wrong codes, request a new one")
    if not otp_matches(phone, code.strip(), otp.code_hash):
        otp.attempts += 1
        left = settings.otp_max_attempts - otp.attempts
        if left <= 0:
            otp.consumed_at = now
        db.commit()
        raise ApiError(400, "otp_invalid", "Wrong code", details={"attemptsLeft": max(left, 0)})
    otp.consumed_at = now


def verify_otp(db: Session, data: schemas.OtpVerifyIn) -> schemas.TokenOut:
    phone = normalize_phone(data.phone)
    user = db.scalar(select(User).where(User.phone == phone))
    is_new = user is None
    if is_new and not (data.full_name and data.full_name.strip()):
        # Checked before the code is used, so the app can ask for the name
        # and retry with the same code.
        raise ApiError(422, "full_name_required", "Full name is required for new users")
    if user is not None and not user.is_active:
        raise ApiError(403, "user_disabled", "This account is disabled")

    _consume_otp(db, phone, data.code)

    if is_new:
        user = User(phone=phone, full_name=data.full_name.strip(),
                    language=(data.language or "en"))
        db.add(user)
        db.flush()

    out = _issue_tokens(db, user, data.device_name, is_new)
    db.commit()
    return out


# -------------------------------------------------------------------- tokens


def _issue_tokens(db: Session, user: User, device_name: str | None, is_new: bool) -> schemas.TokenOut:
    access, expires_in = create_access_token(user.id)
    refresh = generate_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        token_hash=sha256_hex(refresh),
        device_name=device_name,
        expires_at=now_utc() + timedelta(days=settings.refresh_token_days),
    )
    db.add(rt)
    db.flush()
    return schemas.TokenOut(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        is_new_user=is_new,
        user=schemas.UserOut.model_validate(user),
    )


def refresh_tokens(db: Session, data: schemas.RefreshIn) -> schemas.TokenOut:
    now = now_utc()
    rt = db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == sha256_hex(data.refresh_token))
        .with_for_update()
    )
    if rt is None:
        raise ApiError(401, "refresh_invalid", "Invalid refresh token")
    if rt.revoked_at is not None:
        # A rotated token was used again: it may have been stolen.
        # Sign out every device of this user.
        revoke_all(db, rt.user_id)
        db.commit()
        raise ApiError(401, "refresh_reused", "Session expired, sign in again")
    if rt.expires_at <= now:
        raise ApiError(401, "refresh_expired", "Session expired, sign in again")

    user = db.get(User, rt.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "unauthorized", "User not found or disabled")

    out = _issue_tokens(db, user, data.device_name or rt.device_name, False)
    new_rt = db.scalar(select(RefreshToken).where(
        RefreshToken.token_hash == sha256_hex(out.refresh_token)))
    rt.revoked_at = now
    rt.replaced_by = new_rt.id
    db.commit()
    return out


def logout(db: Session, refresh_token: str) -> None:
    rt = db.scalar(select(RefreshToken).where(
        RefreshToken.token_hash == sha256_hex(refresh_token)))
    if rt is not None and rt.revoked_at is None:
        rt.revoked_at = now_utc()
        db.commit()


def revoke_all(db: Session, user_id) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
