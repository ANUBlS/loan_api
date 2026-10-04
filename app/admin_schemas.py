"""Request/response models for the admin panel (/api/v1/admin/*). camelCase JSON."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from pydantic import Field

from .models import AdminRole, LoanType, PaymentMethod
from .schemas import ApiModel, InstallmentOut, LoanSummaryOut, Money

T = TypeVar("T")


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------- admin users

Username = Field(min_length=3, max_length=60, pattern=r"^[a-zA-Z0-9._-]+$")


class AdminUserOut(ApiModel):
    id: uuid.UUID
    username: str
    full_name: str
    role: AdminRole
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    locked_until: datetime | None
    created_at: datetime


class AdminLoginIn(ApiModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class AdminTokenOut(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    admin: AdminUserOut


class AdminUserCreateIn(ApiModel):
    username: str = Username
    full_name: str = Field(min_length=2, max_length=120)
    role: AdminRole
    password: str = Field(min_length=1, max_length=200)
    must_change_password: bool = True


class AdminUserUpdateIn(ApiModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    role: AdminRole | None = None
    is_active: bool | None = None


class PasswordResetIn(ApiModel):
    new_password: str = Field(min_length=1, max_length=200)
    must_change_password: bool = True


class ChangePasswordIn(ApiModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


# --------------------------------------------------------------- dashboard


class MonthTotal(ApiModel):
    month: str  # YYYY-MM
    count: int
    amount: Money


class DashboardOut(ApiModel):
    customers_total: int
    customers_blocked: int
    loans_open: int
    loans_overdue: int
    loans_closed: int
    outstanding_principal: Money
    overdue_amount: Money
    payments_today_count: int
    payments_today_amount: Money
    payments_month_amount: Money
    applications_pending: int
    collections_by_month: list[MonthTotal]
    currency: str


# ---------------------------------------------------------------- products


class ProductAdminOut(ApiModel):
    id: int
    code: str
    type: LoanType
    name_key: str
    loan_name_key: str
    annual_rate: Money
    min_amount: Money
    max_amount: Money
    step: Money
    min_term: int
    max_term: int
    is_active: bool
    sort_order: int


class ProductCreateIn(ApiModel):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[a-z0-9_-]+$")
    type: LoanType
    name_key: str = Field(min_length=2, max_length=60)
    loan_name_key: str = Field(min_length=2, max_length=60)
    annual_rate: Decimal = Field(ge=0, le=100, decimal_places=2)
    min_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    max_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    step: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    min_term: int = Field(ge=1, le=600)
    max_term: int = Field(ge=1, le=600)
    is_active: bool = True
    sort_order: int = 0


class ProductUpdateIn(ApiModel):
    name_key: str | None = Field(default=None, min_length=2, max_length=60)
    loan_name_key: str | None = Field(default=None, min_length=2, max_length=60)
    annual_rate: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    min_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    max_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    step: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    min_term: int | None = Field(default=None, ge=1, le=600)
    max_term: int | None = Field(default=None, ge=1, le=600)
    is_active: bool | None = None
    sort_order: int | None = None


# --------------------------------------------------------------- customers


class CustomerOut(ApiModel):
    id: uuid.UUID
    phone: str
    full_name: str
    language: str
    is_active: bool
    created_at: datetime
    loans_total: int
    loans_open: int
    loans_overdue: int
    outstanding_principal: Money
    active_sessions: int


class CustomerCreateIn(ApiModel):
    phone: str = Field(examples=["+994 50 123 45 67"])
    full_name: str = Field(min_length=3, max_length=120)
    language: str = Field(default="az", min_length=2, max_length=5)


class CustomerUpdateIn(ApiModel):
    phone: str | None = None
    full_name: str | None = Field(default=None, min_length=3, max_length=120)
    language: str | None = Field(default=None, min_length=2, max_length=5)


class ResetAccessOut(ApiModel):
    sessions_revoked: int
    otp_codes_cleared: int


# ------------------------------------------------------------------- loans

LoanStateFilter = Literal["open", "overdue", "active", "closed"]


class AdminInstallmentOut(InstallmentOut):
    id: int
    payment_id: uuid.UUID | None
    payment_reference: str | None


class AdminLoanOut(LoanSummaryOut):
    user_id: uuid.UUID
    user_phone: str
    user_full_name: str
    product_id: int
    application_id: uuid.UUID | None
    created_at: datetime


class AdminLoanDetailOut(AdminLoanOut):
    schedule: list[AdminInstallmentOut]
    principal_difference: Money = Field(
        description="Loan amount minus the sum of all installment principals "
                    "(0 unless installments were edited by hand)"
    )


class LoanCreateIn(ApiModel):
    user_id: uuid.UUID
    product_id: int
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    annual_rate: Decimal | None = Field(
        default=None, ge=0, le=100, description="Defaults to the product rate")
    term_months: int = Field(ge=1, le=600)
    start_date: date | None = Field(default=None, description="Defaults to today (Asia/Baku)")
    contract_no: str | None = Field(default=None, min_length=3, max_length=30)
    check_product_limits: bool = Field(
        default=True, description="Reject amount/term outside the product limits")


class LoanUpdateIn(ApiModel):
    """contractNo can always change. Amount, rate, term, start date and product only
    while the loan has no payments — the schedule and documents are then rebuilt."""
    contract_no: str | None = Field(default=None, min_length=3, max_length=30)
    product_id: int | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    annual_rate: Decimal | None = Field(default=None, ge=0, le=100)
    term_months: int | None = Field(default=None, ge=1, le=600)
    start_date: date | None = None


class InstallmentUpdateIn(ApiModel):
    due_date: date | None = None
    principal: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    interest: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)


class RestructureIn(ApiModel):
    """Rebuilds all unpaid installments as a new annuity on the outstanding principal."""
    annual_rate: Decimal | None = Field(default=None, ge=0, le=100)
    term_months: int | None = Field(
        default=None, ge=1, le=600, description="Number of remaining installments")
    first_due_date: date | None = Field(
        default=None, description="Due date of the first new installment")


# ---------------------------------------------------------------- payments


class AdminPaymentOut(ApiModel):
    id: uuid.UUID
    reference: str
    loan_id: uuid.UUID
    contract_no: str
    user_id: uuid.UUID
    user_phone: str
    user_full_name: str
    installment_number: int
    amount: Money
    currency: str
    paid_at: datetime
    method: PaymentMethod
    note: str | None
    created_by: str | None
    reversed_at: datetime | None
    reversed_by: str | None
    reversal_reason: str | None
    created_at: datetime


class PaymentCreateIn(ApiModel):
    """Pays the next unpaid installment(s) of the loan (overdue first)."""
    loan_id: uuid.UUID
    installments: int = Field(default=1, ge=1, le=600, description="How many to pay")
    paid_at: datetime | None = Field(default=None, description="Defaults to now")
    method: PaymentMethod = PaymentMethod.cash
    note: str | None = Field(default=None, max_length=500)


class PaymentReverseIn(ApiModel):
    reason: str = Field(min_length=3, max_length=500)


# --------------------------------------------------------------- documents


class AdminDocumentOut(ApiModel):
    id: uuid.UUID
    loan_id: uuid.UUID
    name_key: str
    file_name: str
    content_type: str
    size_bytes: int
    sort_order: int
    created_at: datetime


class DocumentUpdateIn(ApiModel):
    name_key: str | None = Field(default=None, min_length=2, max_length=40)
    file_name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = None


# ------------------------------------------------------------------- audit


class AuditOut(ApiModel):
    id: int
    admin_id: uuid.UUID | None
    admin_username: str
    action: str
    entity: str
    entity_id: str | None
    details: dict | None
    ip: str | None
    created_at: datetime
