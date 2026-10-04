"""Back-office API for the admin panel (repo ANUBlS/loan_admin).

Sign in with POST /admin/auth/login and send `Authorization: Bearer <token>`.
Scripts can still use the header `X-Admin-Key` (acts as role "admin").

Roles: viewer = read only · operator = customers, loans, schedules, payments,
documents, applications · admin = everything + admin users, products, loan
deletion and payment reversal. Every change is written to the audit log.
"""

import uuid
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status

from .. import admin_schemas as A
from .. import schemas
from ..config import settings
from ..deps import DB, CurrentAdmin, Operator, SuperAdmin, Viewer
from ..errors import ApiError
from ..models import ApplicationStatus, PaymentMethod
from ..services import admin_accounts as accounts
from ..services import applications as app_service
from ..services import backoffice as bo
from ..services import loans as loan_service

router = APIRouter(prefix="/admin", tags=["Admin"])

Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


def _ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


async def _read_upload(file: UploadFile) -> bytes:
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ApiError(413, "file_too_large", f"Maximum file size is {settings.max_upload_mb} MB",
                       details={"maxMb": settings.max_upload_mb})
    return data


# --------------------------------------------------------------------- auth


@router.post("/auth/login", response_model=A.AdminTokenOut, tags=["Admin: auth"])
def login(data: A.AdminLoginIn, request: Request, db: DB):
    """5 wrong passwords lock the account for 15 minutes."""
    return accounts.login(db, data, _ip(request))


@router.get("/auth/me", response_model=A.AdminUserOut, tags=["Admin: auth"])
def me(admin: CurrentAdmin):
    if admin.id is None:
        raise ApiError(400, "not_supported", "Not available with X-Admin-Key")
    return accounts.admin_out(admin)


@router.post("/auth/change-password", response_model=A.AdminTokenOut, tags=["Admin: auth"])
def change_password(data: A.ChangePasswordIn, admin: CurrentAdmin, request: Request, db: DB):
    """Returns a new token; other sessions of this admin are signed out."""
    return accounts.change_own_password(db, admin, data, _ip(request))


# -------------------------------------------------------------- admin users


@router.get("/admin-users", response_model=list[A.AdminUserOut], tags=["Admin: users"])
def list_admins(_: SuperAdmin, db: DB):
    return accounts.list_admins(db)


@router.post("/admin-users", response_model=A.AdminUserOut,
             status_code=status.HTTP_201_CREATED, tags=["Admin: users"])
def create_admin(data: A.AdminUserCreateIn, admin: SuperAdmin, request: Request, db: DB):
    return accounts.create_admin(db, admin, data, _ip(request))


@router.patch("/admin-users/{admin_id}", response_model=A.AdminUserOut, tags=["Admin: users"])
def update_admin(admin_id: uuid.UUID, data: A.AdminUserUpdateIn, admin: SuperAdmin,
                 request: Request, db: DB):
    return accounts.update_admin(db, admin, admin_id, data, _ip(request))


@router.post("/admin-users/{admin_id}/reset-password", response_model=A.AdminUserOut,
             tags=["Admin: users"])
def reset_admin_password(admin_id: uuid.UUID, data: A.PasswordResetIn, admin: SuperAdmin,
                         request: Request, db: DB):
    return accounts.reset_admin_password(db, admin, admin_id, data, _ip(request))


# ---------------------------------------------------------------- dashboard


@router.get("/dashboard", response_model=A.DashboardOut, tags=["Admin: dashboard"])
def dashboard(_: Viewer, db: DB):
    return bo.dashboard(db)


# ----------------------------------------------------------------- products


@router.get("/products", response_model=list[A.ProductAdminOut], tags=["Admin: products"])
def list_products(_: Viewer, db: DB):
    return bo.list_products(db)


@router.post("/products", response_model=A.ProductAdminOut,
             status_code=status.HTTP_201_CREATED, tags=["Admin: products"])
def create_product(data: A.ProductCreateIn, admin: SuperAdmin, request: Request, db: DB):
    return bo.create_product(db, admin, data, _ip(request))


@router.patch("/products/{product_id}", response_model=A.ProductAdminOut, tags=["Admin: products"])
def update_product(product_id: int, data: A.ProductUpdateIn, admin: SuperAdmin,
                   request: Request, db: DB):
    return bo.update_product(db, admin, product_id, data, _ip(request))


# ---------------------------------------------------------------- customers


@router.get("/customers", response_model=A.Page[A.CustomerOut], tags=["Admin: customers"])
def list_customers(
    _: Viewer, db: DB,
    q: str | None = Query(None, description="Name or phone digits"),
    status_: str | None = Query(None, alias="status", pattern="^(active|blocked)$"),
    limit: Limit = 50, offset: Offset = 0,
):
    return bo.list_customers(db, q, status_, limit, offset)


@router.post("/customers", response_model=A.CustomerOut,
             status_code=status.HTTP_201_CREATED, tags=["Admin: customers"])
def create_customer(data: A.CustomerCreateIn, admin: Operator, request: Request, db: DB):
    """The customer signs in to the app later with this phone number + SMS code."""
    return bo.create_customer(db, admin, data, _ip(request))


@router.get("/customers/{user_id}", response_model=A.CustomerOut, tags=["Admin: customers"])
def get_customer(user_id: uuid.UUID, _: Viewer, db: DB):
    return bo.customer_out(db, bo.get_user(db, user_id))


@router.patch("/customers/{user_id}", response_model=A.CustomerOut, tags=["Admin: customers"])
def update_customer(user_id: uuid.UUID, data: A.CustomerUpdateIn, admin: Operator,
                    request: Request, db: DB):
    """Changing the phone signs the customer out of all devices."""
    return bo.update_customer(db, admin, user_id, data, _ip(request))


@router.post("/customers/{user_id}/block", response_model=A.CustomerOut, tags=["Admin: customers"])
def block_customer(user_id: uuid.UUID, admin: Operator, request: Request, db: DB):
    """Blocks app sign-in and signs out all devices."""
    return bo.set_customer_active(db, admin, user_id, False, _ip(request))


@router.post("/customers/{user_id}/unblock", response_model=A.CustomerOut,
             tags=["Admin: customers"])
def unblock_customer(user_id: uuid.UUID, admin: Operator, request: Request, db: DB):
    return bo.set_customer_active(db, admin, user_id, True, _ip(request))


@router.post("/customers/{user_id}/reset-access", response_model=A.ResetAccessOut,
             tags=["Admin: customers"])
def reset_customer_access(user_id: uuid.UUID, admin: Operator, request: Request, db: DB):
    """'Password reset' for app customers: signs out every device and clears SMS-code
    limits. Next login: new SMS code, then the app asks for a new 6-digit PIN."""
    return bo.reset_access(db, admin, user_id, _ip(request))


# -------------------------------------------------------------------- loans


@router.get("/loans", response_model=A.Page[A.AdminLoanOut], tags=["Admin: loans"])
def list_loans(
    _: Viewer, db: DB,
    q: str | None = Query(None, description="Contract no, name or phone digits"),
    state: A.LoanStateFilter | None = None,
    user_id: uuid.UUID | None = Query(None, alias="userId"),
    limit: Limit = 50, offset: Offset = 0,
):
    return bo.list_loans(db, q, state, user_id, limit, offset)


@router.post("/loans", response_model=A.AdminLoanDetailOut,
             status_code=status.HTTP_201_CREATED, tags=["Admin: loans"])
def create_loan(data: A.LoanCreateIn, admin: Operator, request: Request, db: DB):
    """Creates the loan with its annuity schedule and the standard PDF documents."""
    return bo.create_loan(db, admin, data, _ip(request))


@router.get("/loans/{loan_id}", response_model=A.AdminLoanDetailOut, tags=["Admin: loans"])
def get_loan(loan_id: uuid.UUID, _: Viewer, db: DB):
    return bo.get_loan_detail(db, loan_id)


@router.patch("/loans/{loan_id}", response_model=A.AdminLoanDetailOut, tags=["Admin: loans"])
def update_loan(loan_id: uuid.UUID, data: A.LoanUpdateIn, admin: Operator,
                request: Request, db: DB):
    return bo.update_loan(db, admin, loan_id, data, _ip(request))


@router.delete("/loans/{loan_id}", status_code=status.HTTP_204_NO_CONTENT,
               tags=["Admin: loans"])
def delete_loan(loan_id: uuid.UUID, admin: SuperAdmin, request: Request, db: DB):
    """Only loans without (unreversed) payments."""
    bo.delete_loan(db, admin, loan_id, _ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/loans/{loan_id}/installments/{number}", response_model=A.AdminLoanDetailOut,
              tags=["Admin: schedule"])
def update_installment(loan_id: uuid.UUID, number: int, data: A.InstallmentUpdateIn,
                       admin: Operator, request: Request, db: DB):
    """Edit one unpaid installment (due date, principal, interest). Balances are
    recalculated and the schedule PDF is regenerated."""
    return bo.update_installment(db, admin, loan_id, number, data, _ip(request))


@router.post("/loans/{loan_id}/restructure", response_model=A.AdminLoanDetailOut,
             tags=["Admin: schedule"])
def restructure_loan(loan_id: uuid.UUID, data: A.RestructureIn, admin: Operator,
                     request: Request, db: DB):
    """Replaces all unpaid installments with a new annuity on the outstanding
    principal (new rate / number of installments / first due date)."""
    return bo.restructure(db, admin, loan_id, data, _ip(request))


@router.post("/loans/{loan_id}/documents/regenerate", status_code=status.HTTP_204_NO_CONTENT,
             tags=["Admin: documents"])
def regenerate_documents(loan_id: uuid.UUID, admin: Operator, request: Request, db: DB):
    """Rebuilds the six generated PDFs; uploaded documents are kept."""
    bo.regenerate_documents(db, admin, loan_id, _ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------- payments


@router.get("/payments", response_model=A.Page[A.AdminPaymentOut], tags=["Admin: payments"])
def list_payments(
    _: Viewer, db: DB,
    q: str | None = Query(None, description="Reference, contract no, name or phone"),
    loan_id: uuid.UUID | None = Query(None, alias="loanId"),
    user_id: uuid.UUID | None = Query(None, alias="userId"),
    method: PaymentMethod | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    include_reversed: bool = Query(True, alias="includeReversed"),
    limit: Limit = 50, offset: Offset = 0,
):
    return bo.list_payments(db, q, loan_id, user_id, method.value if method else None,
                            date_from, date_to, include_reversed, limit, offset)


@router.post("/payments", response_model=list[A.AdminPaymentOut],
             status_code=status.HTTP_201_CREATED, tags=["Admin: payments"])
def create_payment(data: A.PaymentCreateIn, admin: Operator, request: Request, db: DB):
    """Records a payment (cash, bank transfer, card) for the next unpaid
    installment(s) of the loan, overdue first."""
    return bo.create_payments(db, admin, data, _ip(request))


@router.post("/payments/{payment_id}/reverse", response_model=A.AdminPaymentOut,
             tags=["Admin: payments"])
def reverse_payment(payment_id: uuid.UUID, data: A.PaymentReverseIn, admin: SuperAdmin,
                    request: Request, db: DB):
    """Cancels a wrong payment; the installment becomes unpaid again.
    Only the latest payment of a loan can be reversed."""
    return bo.reverse_payment(db, admin, payment_id, data.reason, _ip(request))


# ---------------------------------------------------------------- documents


@router.get("/loans/{loan_id}/documents", response_model=list[A.AdminDocumentOut],
            tags=["Admin: documents"])
def list_documents(loan_id: uuid.UUID, _: Viewer, db: DB):
    return bo.list_documents(db, loan_id)


@router.post("/loans/{loan_id}/documents", response_model=A.AdminDocumentOut,
             status_code=status.HTTP_201_CREATED, tags=["Admin: documents"])
async def upload_document(
    loan_id: uuid.UUID, admin: Operator, request: Request, db: DB,
    file: UploadFile = File(...),
    name_key: str = Form("doc.other", alias="nameKey", min_length=2, max_length=40,
                         description="Shown in the app via translation, e.g. doc.agreement"),
):
    """PDF, JPG, PNG, WEBP, DOC, DOCX up to MAX_UPLOAD_MB (stored in the database)."""
    content = await _read_upload(file)
    return bo.upload_document(db, admin, loan_id, name_key, file.filename,
                              file.content_type, content, _ip(request))


@router.put("/documents/{document_id}/file", response_model=A.AdminDocumentOut,
            tags=["Admin: documents"])
async def replace_document(document_id: uuid.UUID, admin: Operator, request: Request, db: DB,
                           file: UploadFile = File(...)):
    content = await _read_upload(file)
    return bo.replace_document_file(db, admin, document_id, file.filename,
                                    file.content_type, content, _ip(request))


@router.patch("/documents/{document_id}", response_model=A.AdminDocumentOut,
              tags=["Admin: documents"])
def update_document(document_id: uuid.UUID, data: A.DocumentUpdateIn, admin: Operator,
                    request: Request, db: DB):
    return bo.update_document(db, admin, document_id, data, _ip(request))


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT,
               tags=["Admin: documents"])
def delete_document(document_id: uuid.UUID, admin: Operator, request: Request, db: DB):
    bo.delete_document(db, admin, document_id, _ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}/download", response_class=Response,
            tags=["Admin: documents"])
def download_document(document_id: uuid.UUID, _: Viewer, db: DB,
                      inline: bool = Query(False, description="Open in the browser")):
    doc = bo.get_document(db, document_id)
    db.refresh(doc, ["content"])
    disposition = "inline" if inline else "attachment"
    return Response(
        content=doc.content, media_type=doc.content_type,
        headers={
            "Content-Disposition":
                f"{disposition}; filename*=UTF-8''{quote(doc.file_name)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ------------------------------------------------------------- applications


@router.get("/applications", response_model=list[schemas.AdminApplicationOut],
            tags=["Admin: applications"])
def list_applications(
    _: Viewer, db: DB,
    status_: ApplicationStatus | None = Query(ApplicationStatus.submitted, alias="status"),
    limit: Limit = 100, offset: Offset = 0,
):
    return [app_service.admin_application_out(a)
            for a in app_service.list_for(db, None, status_, limit, offset)]


@router.post("/applications/{application_id}/approve", response_model=schemas.LoanDetailOut,
             tags=["Admin: applications"])
def approve(application_id: uuid.UUID, data: schemas.ApproveIn, admin: Operator,
            request: Request, db: DB):
    """Approve → creates the loan (contract no, schedule, documents) and returns it."""
    _, loan = app_service.approve(db, application_id, data)
    bo.audit(db, admin, "application.approve", "application", application_id,
             {"loanId": loan.id, "contractNo": loan.contract_no,
              **data.model_dump(exclude_none=True, by_alias=True)}, _ip(request))
    db.commit()
    loan = loan_service.get_user_loan(db, loan.user, loan.id)
    return loan_service.loan_detail(loan)


@router.post("/applications/{application_id}/reject",
             response_model=schemas.AdminApplicationOut, tags=["Admin: applications"])
def reject(application_id: uuid.UUID, data: schemas.RejectIn, admin: Operator,
           request: Request, db: DB):
    out = app_service.admin_application_out(
        app_service.reject(db, application_id, data.reason))
    bo.audit(db, admin, "application.reject", "application", application_id,
             {"reason": data.reason}, _ip(request))
    db.commit()
    return out


# -------------------------------------------------------------------- audit


@router.get("/audit", response_model=A.Page[A.AuditOut], tags=["Admin: audit"])
def list_audit(
    _: SuperAdmin, db: DB,
    entity: str | None = None,
    entity_id: str | None = Query(None, alias="entityId"),
    admin_id: uuid.UUID | None = Query(None, alias="adminId"),
    action: str | None = Query(None, description="e.g. payment.* or loan.create"),
    limit: Limit = 100, offset: Offset = 0,
):
    return bo.list_audit(db, entity, entity_id, admin_id, action, limit, offset)
