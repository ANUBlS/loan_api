import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import timedelta

import jwt

from .config import settings
from .errors import ApiError
from .timeutils import now_utc

_PHONE_RE = re.compile(r"^994\d{9}$")


def normalize_phone(raw: str) -> str:
    """Accepts '+994 50 123 45 67', '994501234567', '0501234567', '501234567'
    and returns E.164: '+994501234567'. Raises ApiError otherwise."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10 and digits.startswith("0"):
        digits = "994" + digits[1:]
    elif len(digits) == 9:
        digits = "994" + digits
    if not _PHONE_RE.match(digits):
        raise ApiError(422, "phone_invalid", "Phone must be an Azerbaijani number: +994XXXXXXXXX")
    return "+" + digits


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ----------------------------------------------------------------------- OTP


def generate_otp() -> str:
    return f"{secrets.randbelow(10 ** settings.otp_length):0{settings.otp_length}d}"


def hash_otp(phone: str, code: str) -> str:
    return hmac.new(
        settings.otp_secret.encode(), f"{phone}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def otp_matches(phone: str, code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(phone, code), code_hash)


# ----------------------------------------------------------------------- JWT


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    now = now_utc()
    ttl = timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> uuid.UUID:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "token_expired", "Access token expired",
                       headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise ApiError(401, "token_invalid", "Invalid access token",
                       headers={"WWW-Authenticate": "Bearer"})
    if payload.get("type") != "access":
        raise ApiError(401, "token_invalid", "Invalid access token")
    try:
        return uuid.UUID(payload["sub"])
    except ValueError:
        raise ApiError(401, "token_invalid", "Invalid access token")


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


# ------------------------------------------------------------ admin panel

_PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 (600k iterations, random salt): pbkdf2_sha256$iter$salt$hash"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations)
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def create_admin_token(admin_id: uuid.UUID, role: str, token_version: int) -> tuple[str, int]:
    now = now_utc()
    ttl = timedelta(minutes=settings.admin_token_minutes)
    payload = {
        "sub": str(admin_id),
        "type": "admin",
        "role": role,
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_admin_token(token: str) -> tuple[uuid.UUID, int]:
    """Returns (admin_id, token_version)."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "token_expired", "Session expired, sign in again",
                       headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise ApiError(401, "token_invalid", "Invalid token",
                       headers={"WWW-Authenticate": "Bearer"})
    if payload.get("type") != "admin":
        raise ApiError(401, "token_invalid", "Not an admin token")
    try:
        return uuid.UUID(payload["sub"]), int(payload.get("ver", 0))
    except (ValueError, TypeError):
        raise ApiError(401, "token_invalid", "Invalid token")
