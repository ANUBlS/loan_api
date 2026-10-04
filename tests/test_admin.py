"""Admin panel API: accounts and roles, customers, loans, schedule, payments,
documents, audit log."""

import uuid
from datetime import date

import pytest

from app.database import SessionLocal
from app.services.loan_math import add_months
from tests.conftest import DEMO_PHONE, auth_header, sign_in

A = "/api/v1/admin"
PASSWORD = "Str0ngPassw0rd"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _create_admin(username: str, role: str) -> None:
    from app import admin_schemas as S
    from app.models import AdminRole
    from app.services.admin_accounts import create_admin

    with SessionLocal() as db:
        create_admin(db, None, S.AdminUserCreateIn(
            username=username, full_name=username.title(), role=AdminRole(role),
            password=PASSWORD, must_change_password=False))


def _login(client, username: str, password: str = PASSWORD) -> dict:
    r = client.post(f"{A}/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


@pytest.fixture
def admin_h(client):
    _create_admin("boss", "admin")
    return _login(client, "boss")


@pytest.fixture
def operator_h(client):
    _create_admin("oper", "operator")
    return _login(client, "oper")


@pytest.fixture
def viewer_h(client):
    _create_admin("view", "viewer")
    return _login(client, "view")


def _product_id(client, h, code="consumer") -> int:
    return next(p["id"] for p in client.get(f"{A}/products", headers=h).json()
                if p["code"] == code)


def _customer(client, h, phone="+994551112233", name="Aynur Mammadova") -> dict:
    r = client.post(f"{A}/customers", headers=h,
                    json={"phone": phone, "fullName": name, "language": "az"})
    assert r.status_code == 201, r.text
    return r.json()


def _loan(client, h, user_id, amount=1200, term=12, start=None) -> dict:
    r = client.post(f"{A}/loans", headers=h, json={
        "userId": user_id, "productId": _product_id(client, h), "amount": amount,
        "termMonths": term, "startDate": str(start or date.today()),
    })
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ accounts


def test_login_lockout_and_roles(client, admin_h, viewer_h):
    for i in range(4):
        r = client.post(f"{A}/auth/login", json={"username": "view", "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["error"]["details"]["attemptsLeft"] == 4 - i
    r = client.post(f"{A}/auth/login", json={"username": "view", "password": "wrong"})
    assert r.status_code == 401
    r = client.post(f"{A}/auth/login", json={"username": "view", "password": PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "login_locked"

    # Viewer can read, not write
    assert client.get(f"{A}/dashboard", headers=viewer_h).status_code == 200
    r = client.post(f"{A}/customers", headers=viewer_h,
                    json={"phone": "+994551112233", "fullName": "No Way"})
    assert r.status_code == 403
    assert client.get(f"{A}/admin-users", headers=viewer_h).status_code == 403

    # App customer tokens are not admin tokens
    tokens = sign_in(client, "+994509998877")
    assert client.get(f"{A}/dashboard", headers=auth_header(tokens)).status_code == 401


def test_admin_user_management(client, admin_h):
    r = client.post(f"{A}/admin-users", headers=admin_h, json={
        "username": "kassir1", "fullName": "Kassir", "role": "operator", "password": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "password_weak"
    r = client.post(f"{A}/admin-users", headers=admin_h, json={
        "username": "kassir1", "fullName": "Kassir", "role": "operator",
        "password": PASSWORD, "mustChangePassword": True})
    assert r.status_code == 201
    kid = r.json()["id"]
    assert r.json()["mustChangePassword"] is True

    kh = _login(client, "kassir1")
    r = client.post(f"{A}/auth/change-password", headers=kh, json={
        "currentPassword": PASSWORD, "newPassword": "N3wPassword99"})
    assert r.status_code == 200 and r.json()["admin"]["mustChangePassword"] is False
    # Old token is revoked by the password change
    assert client.get(f"{A}/auth/me", headers=kh).status_code == 401

    r = client.post(f"{A}/admin-users/{kid}/reset-password", headers=admin_h,
                    json={"newPassword": "Reset12345x"})
    assert r.status_code == 200
    _login(client, "kassir1", "Reset12345x")

    r = client.patch(f"{A}/admin-users/{kid}", headers=admin_h, json={"isActive": False})
    assert r.json()["isActive"] is False
    r = client.post(f"{A}/auth/login", json={"username": "kassir1", "password": "Reset12345x"})
    assert r.status_code == 403

    # Last admin cannot be demoted
    me = client.get(f"{A}/auth/me", headers=admin_h).json()
    r = client.patch(f"{A}/admin-users/{me['id']}", headers=admin_h, json={"role": "viewer"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_admin"


# ----------------------------------------------------------------- customers


def test_customers_create_block_reset(client, operator_h):
    c = _customer(client, operator_h)
    assert c["phone"] == "+994551112233" and c["loansTotal"] == 0

    r = client.post(f"{A}/customers", headers=operator_h,
                    json={"phone": "055 111 22 33", "fullName": "Dup Licate"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "phone_taken"

    # Customer created in the panel can sign in to the app (registered already)
    r = client.post("/api/v1/auth/otp/request", json={"phone": c["phone"]})
    assert r.json()["isRegistered"] is True
    tokens = sign_in(client, c["phone"], None)
    assert client.get("/api/v1/me", headers=auth_header(tokens)).status_code == 200

    r = client.post(f"{A}/customers/{c['id']}/reset-access", headers=operator_h)
    assert r.status_code == 200 and r.json()["sessionsRevoked"] == 1
    r = client.post("/api/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]})
    assert r.status_code == 401

    r = client.post(f"{A}/customers/{c['id']}/block", headers=operator_h)
    assert r.json()["isActive"] is False
    r = client.post("/api/v1/auth/otp/request", json={"phone": c["phone"]})
    code = r.json()["debugCode"]
    r = client.post("/api/v1/auth/otp/verify", json={"phone": c["phone"], "code": code})
    assert r.status_code == 403 and r.json()["error"]["code"] == "user_disabled"
    client.post(f"{A}/customers/{c['id']}/unblock", headers=operator_h)

    r = client.patch(f"{A}/customers/{c['id']}", headers=operator_h,
                     json={"fullName": "Aynur Aliyeva", "phone": "+994701234567"})
    assert r.json()["fullName"] == "Aynur Aliyeva" and r.json()["phone"] == "+994701234567"

    page = client.get(f"{A}/customers", headers=operator_h, params={"q": "aliyeva"}).json()
    assert page["total"] == 1
    page = client.get(f"{A}/customers", headers=operator_h, params={"q": "70123"}).json()
    assert page["total"] == 1


# --------------------------------------------------------- loans + schedule


def test_create_loan_shows_in_app(client, operator_h):
    c = _customer(client, operator_h)
    loan = _loan(client, operator_h, c["id"])
    assert loan["contractNo"].startswith("CL-")
    assert len(loan["schedule"]) == 12 and loan["principalDifference"] == 0
    assert loan["annualRate"] == 17.0  # product rate by default

    r = client.post(f"{A}/loans", headers=operator_h, json={
        "userId": c["id"], "productId": _product_id(client, operator_h), "amount": 10,
        "termMonths": 12})
    assert r.json()["error"]["code"] == "amount_out_of_range"

    docs = client.get(f"{A}/loans/{loan['id']}/documents", headers=operator_h).json()
    assert len(docs) == 6

    tokens = sign_in(client, c["phone"], None)
    app_loans = client.get("/api/v1/loans", headers=auth_header(tokens)).json()["items"]
    assert [l["contractNo"] for l in app_loans] == [loan["contractNo"]]


def test_edit_loan_installment_and_restructure(client, operator_h):
    c = _customer(client, operator_h)
    loan = _loan(client, operator_h, c["id"], amount=1200, term=12)
    lid = loan["id"]

    # Change terms while no payments: schedule rebuilt
    r = client.patch(f"{A}/loans/{lid}", headers=operator_h,
                     json={"amount": 2400, "termMonths": 24, "contractNo": "CL-TEST-1"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["amount"] == 2400 and len(d["schedule"]) == 24 and d["contractNo"] == "CL-TEST-1"

    # Edit an unpaid installment: balances follow
    new_due = str(add_months(date.today(), 2))
    r = client.patch(f"{A}/loans/{lid}/installments/2", headers=operator_h,
                     json={"dueDate": new_due, "principal": 150})
    d = r.json()
    assert d["schedule"][1]["dueDate"] == new_due and d["schedule"][1]["principal"] == 150
    assert d["principalDifference"] != 0
    assert d["schedule"][-1]["balanceAfter"] == 0 or d["principalDifference"] < 0

    # Pay 2 installments, then terms are locked
    r = client.post(f"{A}/payments", headers=operator_h,
                    json={"loanId": lid, "installments": 2, "method": "cash", "note": "kassa"})
    assert r.status_code == 201 and len(r.json()) == 2
    r = client.patch(f"{A}/loans/{lid}", headers=operator_h, json={"amount": 3000})
    assert r.json()["error"]["code"] == "loan_has_payments"
    r = client.patch(f"{A}/loans/{lid}/installments/1", headers=operator_h,
                     json={"principal": 1})
    assert r.json()["error"]["code"] == "installment_paid"

    # Restructure the rest into 6 installments at 10%
    r = client.post(f"{A}/loans/{lid}/restructure", headers=operator_h,
                    json={"termMonths": 6, "annualRate": 10})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["schedule"]) == 8 and d["termMonths"] == 8 and d["annualRate"] == 10
    assert [i["status"] for i in d["schedule"][:2]] == ["paid", "paid"]
    assert d["schedule"][-1]["balanceAfter"] == 0
    assert d["principalDifference"] == 0
    assert [i["number"] for i in d["schedule"]] == list(range(1, 9))


def test_delete_loan_rules(client, admin_h):
    c = _customer(client, admin_h)
    loan = _loan(client, admin_h, c["id"])
    pay = client.post(f"{A}/payments", headers=admin_h, json={"loanId": loan["id"]}).json()[0]
    r = client.delete(f"{A}/loans/{loan['id']}", headers=admin_h)
    assert r.json()["error"]["code"] == "loan_has_payments"
    client.post(f"{A}/payments/{pay['id']}/reverse", headers=admin_h, json={"reason": "test"})
    assert client.delete(f"{A}/loans/{loan['id']}", headers=admin_h).status_code == 204
    assert client.get(f"{A}/loans/{loan['id']}", headers=admin_h).status_code == 404


# ------------------------------------------------------------------ payments


def test_manual_payment_and_reversal(client, admin_h, operator_h):
    c = _customer(client, operator_h)
    loan = _loan(client, operator_h, c["id"])
    lid = loan["id"]

    p1 = client.post(f"{A}/payments", headers=operator_h,
                     json={"loanId": lid, "method": "bank_transfer"}).json()[0]
    p2 = client.post(f"{A}/payments", headers=operator_h, json={"loanId": lid}).json()[0]
    assert (p1["installmentNumber"], p2["installmentNumber"]) == (1, 2)
    assert p1["method"] == "bank_transfer" and p1["createdBy"] == "oper"

    # Operators cannot reverse; only the latest payment can be reversed
    r = client.post(f"{A}/payments/{p2['id']}/reverse", headers=operator_h, json={"reason": "x y z"})
    assert r.status_code == 403
    r = client.post(f"{A}/payments/{p1['id']}/reverse", headers=admin_h, json={"reason": "wrong"})
    assert r.json()["error"]["code"] == "payment_not_last"
    r = client.post(f"{A}/payments/{p2['id']}/reverse", headers=admin_h,
                    json={"reason": "Wrong customer"})
    assert r.status_code == 200 and r.json()["reversedBy"] == "boss"

    d = client.get(f"{A}/loans/{lid}", headers=admin_h).json()
    assert d["paidCount"] == 1 and d["schedule"][1]["status"] != "paid"

    # Reversed installment can be paid again (also from the app)
    tokens = sign_in(client, c["phone"], None)
    r = client.post(f"/api/v1/loans/{lid}/payments", headers={
        **auth_header(tokens), "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 201 and r.json()["payment"]["installmentNumber"] == 2

    # App history hides reversed payments; admin list shows them
    hist = client.get("/api/v1/payments", headers=auth_header(tokens)).json()
    assert hist["total"] == 2
    page = client.get(f"{A}/payments", headers=admin_h, params={"loanId": lid}).json()
    assert page["total"] == 3
    assert {p["method"] for p in page["items"]} == {"app", "cash", "bank_transfer"}
    page = client.get(f"{A}/payments", headers=admin_h,
                      params={"loanId": lid, "includeReversed": "false"}).json()
    assert page["total"] == 2


def test_pay_more_than_left(client, operator_h):
    c = _customer(client, operator_h)
    loan = _loan(client, operator_h, c["id"], amount=600, term=6)
    r = client.post(f"{A}/payments", headers=operator_h,
                    json={"loanId": loan["id"], "installments": 7})
    assert r.json()["error"]["code"] == "too_many_installments"
    r = client.post(f"{A}/payments", headers=operator_h,
                    json={"loanId": loan["id"], "installments": 6})
    assert len(r.json()) == 6
    d = client.get(f"{A}/loans/{loan['id']}", headers=operator_h).json()
    assert d["state"] == "closed"
    r = client.post(f"{A}/payments", headers=operator_h, json={"loanId": loan["id"]})
    assert r.json()["error"]["code"] == "loan_closed"


# ----------------------------------------------------------------- documents


def test_documents_upload_replace_delete(client, operator_h, viewer_h):
    c = _customer(client, operator_h)
    loan = _loan(client, operator_h, c["id"])
    lid = loan["id"]

    r = client.post(f"{A}/loans/{lid}/documents", headers=operator_h,
                    files={"file": ("passport scan.png", PNG, "image/png")},
                    data={"nameKey": "doc.passport"})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["contentType"] == "image/png" and doc["fileName"] == "passport scan.png"

    r = client.post(f"{A}/loans/{lid}/documents", headers=operator_h,
                    files={"file": ("evil.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    assert r.status_code == 415

    r = client.put(f"{A}/documents/{doc['id']}/file", headers=operator_h,
                   files={"file": ("passport.pdf", PDF, "application/pdf")})
    assert r.json()["contentType"] == "application/pdf" and r.json()["sizeBytes"] == len(PDF)

    r = client.get(f"{A}/documents/{doc['id']}/download", headers=viewer_h)
    assert r.status_code == 200 and r.content == PDF

    # The app sees uploaded documents too
    tokens = sign_in(client, c["phone"], None)
    app_docs = client.get(f"/api/v1/loans/{lid}/documents", headers=auth_header(tokens)).json()
    assert "doc.passport" in [d["name"] for d in app_docs]
    r = client.get(f"/api/v1/documents/{doc['id']}/download", headers=auth_header(tokens))
    assert r.content == PDF

    r = client.patch(f"{A}/documents/{doc['id']}", headers=operator_h,
                     json={"nameKey": "doc.id_card", "sortOrder": 0})
    assert r.json()["nameKey"] == "doc.id_card"

    assert client.delete(f"{A}/documents/{doc['id']}", headers=viewer_h).status_code == 403
    assert client.delete(f"{A}/documents/{doc['id']}", headers=operator_h).status_code == 204
    assert len(client.get(f"{A}/loans/{lid}/documents", headers=operator_h).json()) == 6

    r = client.post(f"{A}/loans/{lid}/documents/regenerate", headers=operator_h)
    assert r.status_code == 204


# ------------------------------------------------- dashboard, apps, audit


def test_dashboard_applications_audit(client, admin_h, demo_user):
    h = auth_header(demo_user)
    pid = client.get("/api/v1/catalog").json()["products"][0]["id"]
    app_id = client.post("/api/v1/applications", headers=h, json={
        "productId": pid, "amount": 1000, "termMonths": 12, "purpose": "purpose.personal"
    }).json()["id"]

    d = client.get(f"{A}/dashboard", headers=admin_h).json()
    assert d["customersTotal"] == 1 and d["applicationsPending"] == 1
    assert d["loansOverdue"] == 1 and d["loansClosed"] == 1 and d["loansOpen"] == 3
    assert len(d["collectionsByMonth"]) == 6 and d["currency"] == "AZN"

    apps = client.get(f"{A}/applications", headers=admin_h).json()
    assert [a["id"] for a in apps] == [app_id]
    r = client.post(f"{A}/applications/{app_id}/approve", headers=admin_h, json={})
    assert r.status_code == 200

    loans = client.get(f"{A}/loans", headers=admin_h, params={"state": "overdue"}).json()
    assert loans["total"] == 1 and loans["items"][0]["contractNo"] == "AU-2026-000932"
    loans = client.get(f"{A}/loans", headers=admin_h, params={"q": DEMO_PHONE[-7:]}).json()
    assert loans["total"] == 5

    log = client.get(f"{A}/audit", headers=admin_h).json()
    actions = [e["action"] for e in log["items"]]
    assert "application.approve" in actions and "admin.login" in actions

    # Scripts with X-Admin-Key still work
    r = client.get(f"{A}/dashboard", headers={"X-Admin-Key": "test-admin-key"})
    assert r.status_code == 200
