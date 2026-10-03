# CLAUDE.md — Smart Mobile Loan API

Backend for the Flutter app in github.com/ANUBlS/loan_app. FastAPI + SQLAlchemy 2
+ PostgreSQL, own database (no T24 / core-banking integration). See README.md
for the endpoint list and run instructions.

## Rules

- JSON is camelCase via `ApiModel` (`app/schemas.py`); money fields use the
  `Money` type (Decimal in Python, number in JSON). Never use float for money.
- Errors: raise `ApiError(status, "snake_case_code", "English message", details)`.
  Codes are a contract with the app — don't rename existing ones.
- `productName`, document `name`, `purpose` are the app's translation keys
  (`product.car_loan`, `doc.agreement`, `purpose.car`), not display text.
- Business date = `timeutils.today()` (Asia/Baku). Never `date.today()`.
- Schedule math lives only in `services/loan_math.py` (port of the app's
  `loan_math.dart`). Last installment absorbs rounding.
- Payments lock the loan row (`with_for_update`) and support `Idempotency-Key`.
- Schema changes: edit `models.py`, then `alembic revision --autogenerate -m "..."`,
  review the file, `alembic upgrade head`. Sequences must be added by hand.
- Tests (`pytest`) run against real PostgreSQL (`TEST_DATABASE_URL`); add a test
  for every new endpoint and error code.
- Passcode/biometrics are device-side only; the server authenticates by phone +
  SMS code and JWT. Don't add PIN checks to the API without asking.
