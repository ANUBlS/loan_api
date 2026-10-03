from datetime import timedelta

from sqlalchemy import update

from app.database import SessionLocal
from app.models import OtpCode
from app.timeutils import now_utc

from .conftest import auth_header, sign_in


def test_register_and_profile(client):
    tokens = sign_in(client, "+994 50 765 43 21", "Aysel Mammadova")
    assert tokens["isNewUser"] is True
    assert tokens["user"]["phone"] == "+994507654321"

    r = client.get("/api/v1/me", headers=auth_header(tokens))
    assert r.status_code == 200
    assert r.json()["fullName"] == "Aysel Mammadova"

    r = client.patch("/api/v1/me", json={"language": "ru"}, headers=auth_header(tokens))
    assert r.json()["language"] == "ru"

    # Second sign-in: existing user, no name needed
    again = sign_in(client, "0507654321", None)
    assert again["isNewUser"] is False
    assert again["user"]["id"] == tokens["user"]["id"]


def test_phone_formats(client):
    for raw in ("+994 50 123 45 67", "994501234567", "0501234567", "501234567"):
        r = client.post("/api/v1/auth/otp/request", json={"phone": raw})
        assert r.json()["phone"] == "+994501234567"
    r = client.post("/api/v1/auth/otp/request", json={"phone": "+1 555 0100"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "phone_invalid"


def test_new_user_needs_name_and_code_survives(client):
    code = client.post("/api/v1/auth/otp/request", json={"phone": "+994701112233"}).json()["debugCode"]
    r = client.post("/api/v1/auth/otp/verify", json={"phone": "+994701112233", "code": code})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "full_name_required"
    r = client.post("/api/v1/auth/otp/verify",
                    json={"phone": "+994701112233", "code": code, "fullName": "Elvin Aliyev"})
    assert r.status_code == 200


def test_wrong_code_attempts_then_blocked(client):
    phone = "+994702223344"
    client.post("/api/v1/auth/otp/request", json={"phone": phone})
    for left in (4, 3, 2, 1, 0):
        r = client.post("/api/v1/auth/otp/verify",
                        json={"phone": phone, "code": "000000", "fullName": "X Y Z"})
        assert r.status_code == 400
        assert r.json()["error"]["details"]["attemptsLeft"] == left
    r = client.post("/api/v1/auth/otp/verify",
                    json={"phone": phone, "code": "000000", "fullName": "X Y Z"})
    assert r.json()["error"]["code"] == "otp_expired"  # code burned


def test_expired_code(client):
    phone = "+994703334455"
    code = client.post("/api/v1/auth/otp/request", json={"phone": phone}).json()["debugCode"]
    with SessionLocal() as db:
        db.execute(update(OtpCode).values(expires_at=now_utc() - timedelta(seconds=1)))
        db.commit()
    r = client.post("/api/v1/auth/otp/verify",
                    json={"phone": phone, "code": code, "fullName": "Test User"})
    assert r.json()["error"]["code"] == "otp_expired"


def test_hourly_limit(client):
    phone = "+994704445566"
    for _ in range(5):
        assert client.post("/api/v1/auth/otp/request", json={"phone": phone}).status_code == 200
    r = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "otp_rate_limited"


def test_refresh_rotation_and_reuse_detection(client, new_user):
    old = new_user["refreshToken"]
    r = client.post("/api/v1/auth/refresh", json={"refreshToken": old})
    assert r.status_code == 200
    new = r.json()["refreshToken"]
    assert new != old

    # Reusing the rotated token revokes the whole family
    r = client.post("/api/v1/auth/refresh", json={"refreshToken": old})
    assert r.json()["error"]["code"] == "refresh_reused"
    r = client.post("/api/v1/auth/refresh", json={"refreshToken": new})
    assert r.status_code == 401


def test_logout(client, new_user):
    r = client.post("/api/v1/auth/logout", json={"refreshToken": new_user["refreshToken"]})
    assert r.status_code == 204
    r = client.post("/api/v1/auth/refresh", json={"refreshToken": new_user["refreshToken"]})
    assert r.status_code == 401


def test_protected_endpoints_need_token(client):
    r = client.get("/api/v1/loans")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    r = client.get("/api/v1/loans", headers={"Authorization": "Bearer nonsense"})
    assert r.json()["error"]["code"] == "token_invalid"
