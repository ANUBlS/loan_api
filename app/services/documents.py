"""Generates the standard document pack for every loan (PDF, stored in DB)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..errors import ApiError
from ..models import Document, Loan, User
from .pdf import build_pdf

# Same keys and order as the app's MockData.documents
DOCUMENT_KEYS: list[tuple[str, str, str]] = [
    ("doc.application", "application", "Loan Application"),
    ("doc.schedule", "schedule", "Repayment Schedule"),
    ("doc.bureau", "credit-bureau", "Credit Bureau Consent"),
    ("doc.agreement", "agreement", "Loan Agreement"),
    ("doc.insurance", "insurance", "Insurance Certificate"),
    ("doc.disbursement", "disbursement", "Disbursement Confirmation"),
]

_TYPE_NAMES = {"consumer": "Consumer loan", "car": "Car loan",
               "mortgage": "Mortgage", "business": "Business loan"}


def _num(v) -> str:
    return f"{v:,.2f}".replace(",", " ")  # 12 345.60, like the app


def _money(v, cur: str) -> str:
    return f"{_num(v)} {cur}"


def _header(loan: Loan, user: User) -> list[str]:
    return [
        f"Contract no:      {loan.contract_no}",
        f"Borrower:         {user.full_name}",
        f"Phone:            {user.phone}",
        f"Product:          {_TYPE_NAMES.get(loan.type.value, loan.type.value)}",
        f"Amount:           {_money(loan.amount, loan.currency)}",
        f"Annual rate:      {loan.annual_rate}%",
        f"Term:             {loan.term_months} months",
        f"Start date:       {loan.start_date:%d.%m.%Y}",
        "",
    ]


def _schedule_lines(loan: Loan) -> list[str]:
    lines = [f"{'No':>4}  {'Due date':<10}  {'Principal':>12}  {'Interest':>10}  "
             f"{'Total':>12}  {'Balance':>13}", "-" * 72]
    for i in loan.installments:
        lines.append(
            f"{i.number:>4}  {i.due_date:%d.%m.%Y}  {_num(i.principal):>12}  "
            f"{_num(i.interest):>10}  {_num(i.total):>12}  {_num(i.balance_after):>13}"
        )
    return lines


def _body(key: str, loan: Loan, user: User) -> list[str]:
    head = _header(loan, user)
    if key == "doc.schedule":
        return head + _schedule_lines(loan)
    if key == "doc.agreement":
        first = loan.installments[0] if loan.installments else None
        return head + [
            "The lender provides the borrower the amount above on the terms of",
            "this agreement. The borrower repays it in equal monthly annuity",
            "installments according to the repayment schedule.",
            "",
            f"Monthly payment:  {_money(first.total, loan.currency) if first else '-'}",
            f"Installments:     {len(loan.installments)}",
            "",
            "Generated electronically. Valid without signature.",
        ]
    if key == "doc.disbursement":
        return head + [f"The amount of {_money(loan.amount, loan.currency)} was disbursed",
                       f"to the borrower on {loan.start_date:%d.%m.%Y}."]
    return head + ["Generated electronically."]


STANDARD_KEYS = {k for k, _, _ in DOCUMENT_KEYS}


def regenerate(db: Session, loan: Loan, user: User, keys: set[str] | None = None) -> None:
    """Rebuilds the generated PDFs (all, or only `keys`). Uploaded documents stay."""
    keys = (keys or STANDARD_KEYS) & STANDARD_KEYS
    for doc in db.scalars(select(Document).where(
            Document.loan_id == loan.id, Document.name_key.in_(keys))):
        db.delete(doc)
    db.flush()
    generate_for_loan(db, loan, user, keys)


def generate_for_loan(db: Session, loan: Loan, user: User, keys: set[str] | None = None) -> None:
    for order, (key, slug, title) in enumerate(DOCUMENT_KEYS):
        if keys is not None and key not in keys:
            continue
        content = build_pdf(title, _body(key, loan, user))
        db.add(Document(
            loan_id=loan.id,
            name_key=key,
            file_name=f"{loan.contract_no}-{slug}.pdf",
            content_type="application/pdf",
            size_bytes=len(content),
            content=content,
            sort_order=order,
        ))


def document_out(doc: Document) -> schemas.DocumentOut:
    return schemas.DocumentOut(
        id=doc.id,
        name=doc.name_key,
        file_name=doc.file_name,
        content_type=doc.content_type,
        size_kb=max(1, round(doc.size_bytes / 1024)),
        created_at=doc.created_at,
        download_url=f"/api/v1/documents/{doc.id}/download",
    )


def list_for_loan(db: Session, loan_id: uuid.UUID) -> list[schemas.DocumentOut]:
    docs = db.scalars(
        select(Document).where(Document.loan_id == loan_id).order_by(Document.sort_order)
    ).all()
    return [document_out(d) for d in docs]


def get_for_user(db: Session, user: User, doc_id: uuid.UUID) -> Document:
    doc = db.scalar(
        select(Document)
        .join(Loan, Loan.id == Document.loan_id)
        .where(Document.id == doc_id, Loan.user_id == user.id)
    )
    if doc is None:
        raise ApiError(404, "document_not_found", "Document not found")
    return doc
