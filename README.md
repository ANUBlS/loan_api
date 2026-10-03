# Smart Mobile Loan API

Backend for the Flutter loan app (`loan_app`). FastAPI + PostgreSQL, own
database, no core-banking integration. Every value the app currently takes from
`lib/data/mock_data.dart` now has a real endpoint.

- Interactive docs: `http://localhost:8000/docs` (Swagger) and `/redoc`
- Base path: `/api/v1` · JSON is camelCase (matches the Dart models) · money is a number with 2 decimals · currency `AZN` · dates `YYYY-MM-DD`

## Run it

### Option A: Docker (API + PostgreSQL)

```bash
copy .env.example .env            # Windows (cp on Linux/macOS)
docker compose up -d --build
docker compose exec api python -m scripts.seed --demo
```

### Option B: Windows, local Python 3.11+ and PostgreSQL

```powershell
cd api
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env             # set DATABASE_URL to your PostgreSQL

# once, in psql:  CREATE USER loan WITH PASSWORD 'loan';
#                 CREATE DATABASE loan_api OWNER loan;
#                 CREATE DATABASE loan_api_test OWNER loan;

alembic upgrade head               # create tables
python -m scripts.seed --demo      # products + demo customer +994501234567
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` lets a phone on the same Wi-Fi reach the API at
`http://<your-PC-IP>:8000`. From the Android emulator use `http://10.0.2.2:8000`.

### Tests

```powershell
set TEST_DATABASE_URL=postgresql+psycopg://loan:loan@localhost:5432/loan_api_test
pytest
```

Tests use a real PostgreSQL database (row locks and sequences are part of what
is tested). The test database is wiped on every run.

## Sign-in flow

The 6-digit passcode and biometrics stay on the phone (they unlock the app).
The server identifies the customer by phone number, proven with an SMS code.

1. `POST /auth/otp/request` `{"phone": "+994 50 123 45 67"}` → `{phone, expiresIn, resendIn, isRegistered, debugCode}`
2. If `isRegistered` is false, the app asks for the full name.
3. `POST /auth/otp/verify` `{"phone", "code", "fullName"?, "language"?, "deviceName"?}` → `{accessToken, refreshToken, expiresIn, isNewUser, user}`
4. Send `Authorization: Bearer <accessToken>` on every call. Access tokens live 15 minutes.
5. On `401 token_expired` call `POST /auth/refresh` `{"refreshToken"}` and retry. Refresh tokens rotate: each one works once; reusing an old one signs out every device (`refresh_reused`).
6. Sign out: `POST /auth/logout` `{"refreshToken"}`.

Store the refresh token in secure storage (e.g. `flutter_secure_storage`), not in `shared_preferences`.

In development the SMS text is written to the server log and the code is
returned as `debugCode`. Phone numbers are normalized to `+994XXXXXXXXX`
(`+994 50 123 45 67`, `994501234567`, `0501234567` and `501234567` all work).

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/otp/request` | – | Send SMS code |
| POST | `/auth/otp/verify` | – | Check code → tokens (creates user on first sign-in) |
| POST | `/auth/refresh` | – | New token pair |
| POST | `/auth/logout` | – | Revoke this device's refresh token |
| POST | `/auth/logout-all` | Bearer | Sign out all devices |
| GET / PATCH | `/me` | Bearer | Profile (`fullName`, `language`) |
| GET | `/catalog` | – | Products, purposes, currency (Order a loan screen) |
| POST | `/calculator` | – | Monthly payment / schedule preview |
| GET | `/loans` | Bearer | My loans (overdue → active → closed) + `urgentLoanId` |
| GET | `/loans/{id}` | Bearer | Loan + full schedule |
| GET | `/loans/{id}/schedule` | Bearer | Schedule only |
| POST | `/loans/{id}/payments` | Bearer | Pay next installment (mock), header `Idempotency-Key` |
| GET | `/loans/{id}/documents` | Bearer | Document list |
| GET | `/documents/{id}/download` | Bearer | PDF file |
| GET | `/payments?limit&offset&loanId` | Bearer | Payment history, newest first |
| POST | `/applications` | Bearer | Order a loan |
| GET | `/applications?status=` | Bearer | My applications |
| GET | `/applications/{id}` | Bearer | One application |
| POST | `/applications/{id}/cancel` | Bearer | Withdraw while under review |
| GET | `/admin/applications?status=` | X-Admin-Key | Review queue |
| POST | `/admin/applications/{id}/approve` | X-Admin-Key | Creates the loan (schedule + documents) |
| POST | `/admin/applications/{id}/reject` | X-Admin-Key | Reject with reason |
| GET | `/health` | – | Liveness + DB check |

### Loan object (`GET /loans`, `GET /loans/{id}`)

Same fields the app computes today in `lib/models/loan.dart`, now calculated
on the server with the business date in Asia/Baku:

```json
{
  "id": "…", "type": "car", "productName": "product.car_loan",
  "contractNo": "AU-2026-000932", "currency": "AZN",
  "amount": 25000.0, "annualRate": 14.0, "termMonths": 48,
  "startDate": "2026-01-24", "finalPaymentDate": "2030-01-24",
  "state": "overdue", "monthlyPayment": 683.16,
  "paidCount": 6, "paidTotal": 4098.96, "paidPrincipal": 2418.55,
  "outstandingPrincipal": 22581.45, "overdueCount": 2, "overdueAmount": 1366.32,
  "firstUnpaid": {"number": 7, "dueDate": "2026-08-24", "principal": 419.71,
                  "interest": 263.45, "total": 683.16, "balanceAfter": 22161.74,
                  "paidDate": null, "status": "overdue"},
  "nextInstallment": { … "status": "next" },
  "schedule": [ … ]   // only in GET /loans/{id}
}
```

`productName`, document `name` and application `purpose` are translation keys
(`product.car_loan`, `doc.agreement`, `purpose.car`) — the app keeps showing
them through `context.tr(...)` as now.

### Payments

`POST /loans/{id}/payments` pays the earliest unpaid installment (overdue ones
first), exactly like `LoanRepository.payNext()`. Generate a new UUID per tap and
send it as `Idempotency-Key`; a retried request returns the same payment (200)
instead of paying a second installment. The loan row is locked during payment,
so double taps can never pay the same installment twice. Response:
`{"payment": {...}, "loan": {...updated summary...}}`.

### Applications → loans

`POST /applications` checks the amount/term against the product (`minAmount`,
`maxAmount`, `step`, `minTerm`, `maxTerm`) and purpose; the rate and monthly
payment are set by the server. Status: `submitted` → `approved` / `rejected` /
`cancelled`. Approval (back office, `X-Admin-Key`) creates the loan with contract
number (`CL-`, `AU-`, `MG-`, `BL-` + year + sequence), annuity schedule and the six
PDF documents. Max 3 applications under review per customer.

## Errors

Every error has the same shape; map `code` to a translation key in the app.

```json
{"error": {"code": "otp_invalid", "message": "Wrong code", "details": {"attemptsLeft": 3}}}
```

| HTTP | code |
|---|---|
| 400 | `otp_invalid` (details.attemptsLeft), `otp_expired`, `otp_attempts_exceeded` |
| 401 | `unauthorized`, `token_expired` (→ refresh), `token_invalid`, `refresh_invalid`, `refresh_expired`, `refresh_reused` (→ sign in again) |
| 403 | `forbidden`, `user_disabled` |
| 404 | `loan_not_found`, `document_not_found`, `application_not_found`, `product_not_found` |
| 409 | `loan_closed`, `idempotency_conflict`, `application_not_pending`, `too_many_pending_applications` |
| 422 | `validation_error` (details.fields), `phone_invalid`, `full_name_required`, `amount_out_of_range`, `amount_step`, `term_out_of_range`, `purpose_invalid` |
| 429 | `otp_too_soon` (details.retryAfter, header Retry-After), `otp_rate_limited` |

## Connecting the Flutter app

All screens already read from `LoanRepository`, so only the data layer changes:

| App today | API |
|---|---|
| `AuthService.register(name, phone)` | `otp/request` + `otp/verify` (with `fullName`) |
| `MockData.products`, `MockData.purposes` | `GET /catalog` |
| `MockData.loans()` / `LoanRepository.loans` | `GET /loans` (`urgentLoanId` = `urgentLoan`) |
| `Loan.schedule` | `GET /loans/{id}` |
| `LoanRepository.payNext()` | `POST /loans/{id}/payments` |
| `LoanRepository.paymentHistory` | `GET /payments` |
| `LoanRepository.submitApplication()` | `POST /applications` |
| `MockData.documents` | `GET /loans/{id}/documents` + download |
| Sign out (`AuthService.reset`) | `POST /auth/logout`, then clear local data |

Android: plain `http://` to a development server needs
`android:usesCleartextTraffic="true"` on `<application>` in a debug manifest
(or a network security config). Use HTTPS in production.

## Project layout

```
app/
  main.py            FastAPI app, routers, /health
  config.py          settings from env / .env (refuses weak secrets in production)
  models.py          SQLAlchemy tables
  schemas.py         request/response models (camelCase JSON)
  errors.py          uniform error responses
  security.py        phone normalization, OTP hashing, JWT
  deps.py            current user / admin key dependencies
  routers/           one file per resource
  services/
    auth.py          OTP, tokens, refresh rotation
    loans.py         loan read model, creation, payments
    applications.py  ordering, approve / reject
    loan_math.py     annuity + schedule (Decimal port of loan_math.dart)
    documents.py     PDF document pack per loan
    pdf.py           tiny PDF writer
    sms.py           SMS sender (console in dev — plug your provider here)
alembic/             migrations
scripts/seed.py      products + demo customer
tests/               pytest suite
```

## Before production

- `ENVIRONMENT=production`, long random `JWT_SECRET`, `OTP_SECRET`, `ADMIN_API_KEY` (the app refuses to start otherwise) and `EXPOSE_OTP_IN_RESPONSE=false`.
- Implement a real `SmsSender` in `app/services/sms.py` and register it with `set_sender()` at startup.
- Restrict `CORS_ORIGINS`, run behind HTTPS (nginx), back up PostgreSQL.
- Replace the mock payment in `POST /loans/{id}/payments` with your payment provider when needed.
