import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Sequence,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

MONEY = Numeric(14, 2)
RATE = Numeric(5, 2)

# Human-readable numbers (contract no, application / payment references).
contract_no_seq = Sequence("app_contract_no_seq", metadata=Base.metadata)
application_ref_seq = Sequence("app_application_ref_seq", metadata=Base.metadata)
payment_ref_seq = Sequence("app_payment_ref_seq", metadata=Base.metadata)


class LoanType(str, enum.Enum):
    consumer = "consumer"
    car = "car"
    mortgage = "mortgage"
    business = "business"


class ApplicationStatus(str, enum.Enum):
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


def _enum(e: type[enum.Enum]) -> Enum:
    # Stored as VARCHAR + CHECK, so adding a value later is a simple migration.
    return Enum(
        e,
        native_enum=False,
        length=20,
        values_callable=lambda x: [m.value for m in x],
        validate_strings=True,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------- users


class User(TimestampMixin, Base):
    __tablename__ = "app_users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(5), default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    loans: Mapped[list["Loan"]] = relationship(back_populates="user")


class OtpCode(TimestampMixin, Base):
    __tablename__ = "app_otp_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(TimestampMixin, Base):
    __tablename__ = "app_refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device_name: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column()


# ------------------------------------------------------------------ products


class LoanProduct(Base):
    __tablename__ = "app_loan_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    type: Mapped[LoanType] = mapped_column(_enum(LoanType))
    # Translation keys, same as the app: product.consumer / product.consumer_loan
    name_key: Mapped[str] = mapped_column(String(60))
    loan_name_key: Mapped[str] = mapped_column(String(60))
    annual_rate: Mapped[Decimal] = mapped_column(RATE)
    min_amount: Mapped[Decimal] = mapped_column(MONEY)
    max_amount: Mapped[Decimal] = mapped_column(MONEY)
    step: Mapped[Decimal] = mapped_column(MONEY)
    min_term: Mapped[int] = mapped_column(Integer)
    max_term: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------- loans


class Loan(TimestampMixin, Base):
    __tablename__ = "app_loans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT"), index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("app_loan_products.id"))
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_loan_applications.id"), unique=True
    )
    type: Mapped[LoanType] = mapped_column(_enum(LoanType))
    product_name_key: Mapped[str] = mapped_column(String(60))
    contract_no: Mapped[str] = mapped_column(String(30), unique=True)
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    annual_rate: Mapped[Decimal] = mapped_column(RATE)
    term_months: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)

    user: Mapped[User] = relationship(back_populates="loans")
    product: Mapped[LoanProduct] = relationship()
    installments: Mapped[list["Installment"]] = relationship(
        back_populates="loan",
        order_by="Installment.number",
        cascade="all, delete-orphan",
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="loan",
        order_by="Document.sort_order",
        cascade="all, delete-orphan",
    )


class Installment(Base):
    __tablename__ = "app_installments"
    __table_args__ = (UniqueConstraint("loan_id", "number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_loans.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date)
    principal: Mapped[Decimal] = mapped_column(MONEY)
    interest: Mapped[Decimal] = mapped_column(MONEY)
    balance_after: Mapped[Decimal] = mapped_column(MONEY)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    loan: Mapped[Loan] = relationship(back_populates="installments")

    @property
    def total(self) -> Decimal:
        return self.principal + self.interest

    @property
    def is_paid(self) -> bool:
        return self.paid_at is not None


class Payment(TimestampMixin, Base):
    __tablename__ = "app_payments"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(30), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT"), index=True
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_loans.id", ondelete="CASCADE"), index=True
    )
    installment_id: Mapped[int] = mapped_column(
        ForeignKey("app_installments.id", ondelete="CASCADE"), unique=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))

    loan: Mapped[Loan] = relationship()
    installment: Mapped[Installment] = relationship()


class Document(TimestampMixin, Base):
    __tablename__ = "app_documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_loans.id", ondelete="CASCADE"), index=True
    )
    # Translation key, same as the app: doc.agreement, doc.schedule ...
    name_key: Mapped[str] = mapped_column(String(40))
    file_name: Mapped[str] = mapped_column(String(120))
    content_type: Mapped[str] = mapped_column(String(60), default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    loan: Mapped[Loan] = relationship(back_populates="documents")


# -------------------------------------------------------------- applications


class LoanApplication(TimestampMixin, Base):
    __tablename__ = "app_loan_applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(20), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT"), index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("app_loan_products.id"))
    type: Mapped[LoanType] = mapped_column(_enum(LoanType))
    product_name_key: Mapped[str] = mapped_column(String(60))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    term_months: Mapped[int] = mapped_column(Integer)
    annual_rate: Mapped[Decimal] = mapped_column(RATE)
    monthly_payment: Mapped[Decimal] = mapped_column(MONEY)
    purpose: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[ApplicationStatus] = mapped_column(
        _enum(ApplicationStatus), default=ApplicationStatus.submitted, index=True
    )
    decision_note: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()
    product: Mapped[LoanProduct] = relationship()
    loan: Mapped[Loan | None] = relationship(
        primaryjoin="Loan.application_id == LoanApplication.id",
        viewonly=True,
        uselist=False,
    )
