"""Tests run against a real PostgreSQL database (row locks, sequences).

    TEST_DATABASE_URL=postgresql+psycopg://loan:loan@localhost:5432/loan_api_test pytest

The test database is wiped and recreated on every run — never point it at real data.
"""

import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://loan:loan@localhost:5432/loan_api_test"
)
os.environ["ENVIRONMENT"] = "development"
os.environ["EXPOSE_OTP_IN_RESPONSE"] = "true"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["JWT_SECRET"] = "test-jwt-secret-that-is-at-least-32-bytes"
os.environ["OTP_RESEND_SECONDS"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed import seed_demo, seed_products  # noqa: E402

ADMIN = {"X-Admin-Key": "test-admin-key"}
DEMO_PHONE = "+994501234567"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_db():
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with SessionLocal() as db:
        seed_products(db)
        db.commit()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def sign_in(client, phone: str, name: str | None = "Test User") -> dict:
    r = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    code = r.json()["debugCode"]
    body = {"phone": phone, "code": code, "deviceName": "pytest"}
    if name:
        body["fullName"] = name
    r = client.post("/api/v1/auth/otp/verify", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def auth_header(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['accessToken']}"}


@pytest.fixture
def demo_user(client):
    with SessionLocal() as db:
        products = seed_products(db)
        seed_demo(db, products, DEMO_PHONE, "Demo User", reset=False)
        db.commit()
    tokens = sign_in(client, DEMO_PHONE, None)
    return tokens


@pytest.fixture
def new_user(client):
    return sign_in(client, "+994551112233", "New Customer")
