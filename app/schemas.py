"""Request/response models. JSON uses camelCase to match the Dart models."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel

from .models import ApplicationStatus, LoanType

# Money is stored as NUMERIC and sent to the app as a JSON number with
# 2 decimals (the Dart models use double).
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]

InstallmentStatus = Literal["paid", "overdue", "next", "upcoming"]
LoanState = Literal["overdue", "active", "closed"]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )


# ---------------------------------------------------------------------- auth


class OtpRequestIn(ApiModel):
    phone: str = Field(examples=["+994 50 123 45 67"])


class OtpRequestOut(ApiModel):
    phone: str
    expires_in: int
    resend_in: int
    is_registered: bool
    debug_code: str | None = Field(
        default=None, description="Only in development (EXPOSE_OTP_IN_RESPONSE=true)"
    )


class OtpVerifyIn(ApiModel):
    phone: str
    code: str = Field(min_length=4, max_length=8)
    full_name: str | None = Field(
        default=None, min_length=3, max_length=120,
        description="Required the first time a phone number signs in",
    )
    language: str | None = Field(default=None, max_length=5)
    device_name: str | None = Field(default=None, max_length=120)


class RefreshIn(ApiModel):
    refresh_token: str
    device_name: str | None = Field(default=None, max_length=120)


class LogoutIn(ApiModel):
    refresh_token: str


class UserOut(ApiModel):
    id: uuid.UUID
    phone: str
    full_name: str
    language: str
    created_at: datetime


class UserUpdateIn(ApiModel):
    full_name: str | None = Field(default=None, min_length=3, max_length=120)
    language: str | None = Field(default=None, min_length=2, max_length=5)


class TokenOut(ApiModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    is_new_user: bool
    user: UserOut


# ------------------------------------------------------------------ catalog


class ProductOut(ApiModel):
    id: int
    code: str
    type: LoanType
    name: str = Field(validation_alias="name_key")
    loan_name: str = Field(validation_alias="loan_name_key")
    annual_rate: Money
    min_amount: Money
    max_amount: Money
    step: Money
    min_term: int
    max_term: int


class CatalogOut(ApiModel):
    currency: str
    products: list[ProductOut]
    purposes: list[str]


class CalculatorIn(ApiModel):
    product_id: int
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    term_months: int = Field(gt=0, le=600)
    include_schedule: bool = False


class ScheduleRowOut(ApiModel):
    number: int
    due_date: date
    principal: Money
    interest: Money
    total: Money
    balance_after: Money


class CalculatorOut(ApiModel):
    product_id: int
    amount: Money
    term_months: int
    annual_rate: Money
    currency: str
    monthly_payment: Money
    total_payment: Money
    total_interest: Money
    schedule: list[ScheduleRowOut] | None = None


# --------------------------------------------------------------------- loans


class InstallmentOut(ApiModel):
    number: int
    due_date: date
    principal: Money
    interest: Money
    total: Money
    balance_after: Money
    paid_date: datetime | None
    status: InstallmentStatus


class LoanSummaryOut(ApiModel):
    id: uuid.UUID
    type: LoanType
    product_name: str
    contract_no: str
    currency: str
    amount: Money
    annual_rate: Money
    term_months: int
    start_date: date
    final_payment_date: date | None
    state: LoanState
    monthly_payment: Money
    paid_count: int
    paid_total: Money
    paid_principal: Money
    outstanding_principal: Money
    overdue_count: int
    overdue_amount: Money
    first_unpaid: InstallmentOut | None
    next_installment: InstallmentOut | None


class LoanDetailOut(LoanSummaryOut):
    schedule: list[InstallmentOut]


class LoanListOut(ApiModel):
    items: list[LoanSummaryOut]
    urgent_loan_id: uuid.UUID | None = Field(
        description="Overdue loan first, otherwise the open loan with the nearest due date"
    )


class DocumentOut(ApiModel):
    id: uuid.UUID
    name: str = Field(description="Translation key, e.g. doc.agreement")
    file_name: str
    content_type: str
    size_kb: int
    created_at: datetime
    download_url: str


# ------------------------------------------------------------------ payments


class PaymentOut(ApiModel):
    id: uuid.UUID
    reference: str
    loan_id: uuid.UUID
    contract_no: str
    product_name: str
    installment_number: int
    amount: Money
    currency: str
    paid_at: datetime


class PaymentPageOut(ApiModel):
    items: list[PaymentOut]
    total: int
    limit: int
    offset: int


class PayNextOut(ApiModel):
    payment: PaymentOut
    loan: LoanSummaryOut


# -------------------------------------------------------------- applications


class ApplicationIn(ApiModel):
    product_id: int
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    term_months: int = Field(gt=0, le=600)
    purpose: str = Field(examples=["purpose.personal"])


class ApplicationOut(ApiModel):
    id: uuid.UUID
    reference: str
    type: LoanType
    product_name: str
    amount: Money
    term_months: int
    annual_rate: Money
    monthly_payment: Money
    purpose: str
    currency: str
    status: ApplicationStatus
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None
    loan_id: uuid.UUID | None


class AdminApplicationOut(ApplicationOut):
    user_id: uuid.UUID
    user_phone: str
    user_full_name: str


class ApproveIn(ApiModel):
    start_date: date | None = Field(
        default=None, description="Disbursement date; defaults to today (Asia/Baku)"
    )
    annual_rate: Decimal | None = Field(
        default=None, gt=0, le=100,
        description="Override the product rate for this customer",
    )
    note: str | None = Field(default=None, max_length=500)


class RejectIn(ApiModel):
    reason: str = Field(min_length=3, max_length=500)
