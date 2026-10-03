from sqlalchemy.orm import Session

from ..models import LoanType, application_ref_seq, contract_no_seq, payment_ref_seq

_CONTRACT_PREFIX = {
    LoanType.consumer: "CL",
    LoanType.car: "AU",
    LoanType.mortgage: "MG",
    LoanType.business: "BL",
}


def next_contract_no(db: Session, loan_type: LoanType, year: int) -> str:
    """e.g. CL-2026-000123 (same format as the app's mock data)."""
    n = db.scalar(contract_no_seq.next_value())
    return f"{_CONTRACT_PREFIX[loan_type]}-{year}-{n:06d}"


def next_application_ref(db: Session) -> str:
    return f"APP-{db.scalar(application_ref_seq.next_value()):06d}"


def next_payment_ref(db: Session) -> str:
    return f"PAY-{db.scalar(payment_ref_seq.next_value()):08d}"
