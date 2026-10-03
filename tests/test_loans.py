from .conftest import ADMIN, auth_header


def test_catalog_is_public(client):
    r = client.get("/api/v1/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "AZN"
    assert [p["code"] for p in body["products"]] == ["consumer", "car", "mortgage", "business"]
    consumer = body["products"][0]
    assert consumer["name"] == "product.consumer"
    assert consumer["loanName"] == "product.consumer_loan"
    assert consumer["annualRate"] == 17.0
    assert "purpose.personal" in body["purposes"]


def test_calculator(client):
    pid = client.get("/api/v1/catalog").json()["products"][0]["id"]
    r = client.post("/api/v1/calculator",
                    json={"productId": pid, "amount": 5000, "termMonths": 24, "includeSchedule": True})
    assert r.status_code == 200
    body = r.json()
    assert len(body["schedule"]) == 24
    assert body["schedule"][-1]["balanceAfter"] == 0
    r = client.post("/api/v1/calculator", json={"productId": pid, "amount": 5050, "termMonths": 24})
    assert r.json()["error"]["code"] == "amount_step"


def test_demo_loans_overview(client, demo_user):
    r = client.get("/api/v1/loans", headers=auth_header(demo_user))
    assert r.status_code == 200
    body = r.json()
    states = [l["state"] for l in body["items"]]
    assert states == ["overdue", "active", "active", "closed"]
    car = body["items"][0]
    assert car["contractNo"] == "AU-2026-000932"
    assert car["overdueCount"] == 2
    assert body["urgentLoanId"] == car["id"]
    closed = body["items"][-1]
    assert closed["outstandingPrincipal"] == 0 and closed["firstUnpaid"] is None


def test_loan_detail_and_statuses(client, demo_user):
    h = auth_header(demo_user)
    loans = client.get("/api/v1/loans", headers=h).json()["items"]
    car = client.get(f"/api/v1/loans/{loans[0]['id']}", headers=h).json()
    statuses = [i["status"] for i in car["schedule"]]
    assert statuses[:6] == ["paid"] * 6
    assert statuses[6:8] == ["overdue", "overdue"]
    assert statuses[8] == "next"
    assert set(statuses[9:]) == {"upcoming"}


def test_pay_next_with_idempotency(client, demo_user):
    h = auth_header(demo_user)
    car = client.get("/api/v1/loans", headers=h).json()["items"][0]
    url = f"/api/v1/loans/{car['id']}/payments"

    r1 = client.post(url, headers={**h, "Idempotency-Key": "tap-1"})
    assert r1.status_code == 201
    p = r1.json()["payment"]
    assert p["installmentNumber"] == 7
    assert r1.json()["loan"]["overdueCount"] == 1

    # Same key again (network retry) -> same payment, nothing new paid
    r2 = client.post(url, headers={**h, "Idempotency-Key": "tap-1"})
    assert r2.status_code == 200
    assert r2.json()["payment"]["id"] == p["id"]

    r3 = client.post(url, headers={**h, "Idempotency-Key": "tap-2"})
    assert r3.json()["payment"]["installmentNumber"] == 8
    assert r3.json()["loan"]["state"] == "active"

    hist = client.get("/api/v1/payments?limit=2", headers=h).json()
    assert hist["total"] == 31 + 2
    assert hist["items"][0]["reference"] == r3.json()["payment"]["reference"]


def test_cannot_pay_closed_loan(client, demo_user):
    h = auth_header(demo_user)
    closed = client.get("/api/v1/loans", headers=h).json()["items"][-1]
    r = client.post(f"/api/v1/loans/{closed['id']}/payments", headers=h)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "loan_closed"


def test_other_users_cannot_see_loans(client, demo_user, new_user):
    loan_id = client.get("/api/v1/loans", headers=auth_header(demo_user)).json()["items"][0]["id"]
    r = client.get(f"/api/v1/loans/{loan_id}", headers=auth_header(new_user))
    assert r.status_code == 404
    r = client.post(f"/api/v1/loans/{loan_id}/payments", headers=auth_header(new_user))
    assert r.status_code == 404


def test_documents_download(client, demo_user, new_user):
    h = auth_header(demo_user)
    loan = client.get("/api/v1/loans", headers=h).json()["items"][0]
    docs = client.get(f"/api/v1/loans/{loan['id']}/documents", headers=h).json()
    assert [d["name"] for d in docs] == ["doc.application", "doc.schedule", "doc.bureau",
                                         "doc.agreement", "doc.insurance", "doc.disbursement"]
    r = client.get(docs[1]["downloadUrl"], headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-1.4")
    assert client.get(docs[1]["downloadUrl"], headers=auth_header(new_user)).status_code == 404


def test_application_lifecycle(client, new_user):
    h = auth_header(new_user)
    products = client.get("/api/v1/catalog").json()["products"]
    car = next(p for p in products if p["code"] == "car")

    r = client.post("/api/v1/applications", headers=h, json={
        "productId": car["id"], "amount": 20000, "termMonths": 36, "purpose": "purpose.car"})
    assert r.status_code == 201, r.text
    app = r.json()
    assert app["status"] == "submitted"
    assert app["reference"].startswith("APP-")
    assert app["annualRate"] == 13.5

    # Back office sees and approves it -> real loan with schedule + documents
    queue = client.get("/api/v1/admin/applications", headers=ADMIN).json()
    assert queue[0]["id"] == app["id"] and queue[0]["userPhone"] == "+994551112233"
    r = client.post(f"/api/v1/admin/applications/{app['id']}/approve",
                    headers=ADMIN, json={"note": "OK"})
    assert r.status_code == 200, r.text
    loan = r.json()
    assert loan["contractNo"].startswith("AU-")
    assert len(loan["schedule"]) == 36 and loan["state"] == "active"

    app2 = client.get(f"/api/v1/applications/{app['id']}", headers=h).json()
    assert app2["status"] == "approved" and app2["loanId"] == loan["id"]
    assert len(client.get("/api/v1/loans", headers=h).json()["items"]) == 1

    # Already decided
    r = client.post(f"/api/v1/admin/applications/{app['id']}/reject",
                    headers=ADMIN, json={"reason": "late"})
    assert r.json()["error"]["code"] == "application_not_pending"


def test_application_validation_and_cancel(client, new_user):
    h = auth_header(new_user)
    pid = client.get("/api/v1/catalog").json()["products"][0]["id"]
    bad = client.post("/api/v1/applications", headers=h, json={
        "productId": pid, "amount": 100, "termMonths": 12, "purpose": "purpose.personal"})
    assert bad.json()["error"]["code"] == "amount_out_of_range"
    bad = client.post("/api/v1/applications", headers=h, json={
        "productId": pid, "amount": 1000, "termMonths": 12, "purpose": "holiday"})
    assert bad.json()["error"]["code"] == "purpose_invalid"

    ids = []
    for _ in range(3):
        r = client.post("/api/v1/applications", headers=h, json={
            "productId": pid, "amount": 1000, "termMonths": 12, "purpose": "purpose.personal"})
        ids.append(r.json()["id"])
    r = client.post("/api/v1/applications", headers=h, json={
        "productId": pid, "amount": 1000, "termMonths": 12, "purpose": "purpose.personal"})
    assert r.json()["error"]["code"] == "too_many_pending_applications"

    r = client.post(f"/api/v1/applications/{ids[0]}/cancel", headers=h)
    assert r.json()["status"] == "cancelled"


def test_admin_requires_key(client):
    assert client.get("/api/v1/admin/applications").status_code == 403
    assert client.get("/api/v1/admin/applications",
                      headers={"X-Admin-Key": "wrong"}).status_code == 403
