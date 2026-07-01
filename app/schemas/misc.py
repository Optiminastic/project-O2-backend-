from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ReportReviewStatus, ApprovalStatus, VerificationStatus, PaymentMode


# ---------- Reports ----------
class ReportCreate(BaseModel):
    vendor_id: int | None = None
    client_id: int | None = None
    allocation_id: int | None = None
    project_name: str
    reporting_period: str | None = None
    report_type: str | None = None
    uploaded_file: str | None = None
    submission_date: date | None = None
    internal_reviewer: str | None = None
    remarks: str | None = None


class ReportOut(ReportCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    review_status: ReportReviewStatus
    created_at: datetime


class EmailReportRequest(BaseModel):
    to_email: str | None = None  # defaults to client email
    subject: str | None = None
    body: str | None = None


class EmailLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    to_email: str
    subject: str
    delivery_status: str
    sent_by: str | None
    created_at: datetime


# ---------- Approvals ----------
class ApprovalCreate(BaseModel):
    payee_name: str
    amount: float
    purpose: str | None = None
    vendor_invoice_id: int | None = None
    tax_deductions: float = 0.0
    net_payable: float = 0.0
    bank_details: str | None = None


class ApprovalDecision(BaseModel):
    approve: bool
    comment: str | None = None


class PaymentRelease(BaseModel):
    payment_reference: str


class ApprovalActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    approver_name: str
    approver_role: str
    decision: str
    comments: str | None
    created_at: datetime


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payee_name: str
    amount: float
    purpose: str | None
    vendor_invoice_id: int | None
    tax_deductions: float
    net_payable: float
    bank_details: str | None
    status: ApprovalStatus
    cfo_comment: str | None
    ceo_comment: str | None
    payment_reference: str | None
    released_at: datetime | None
    created_at: datetime


class ApprovalDetail(ApprovalOut):
    actions: list[ApprovalActionOut] = []


# ---------- Payment receipts (client money in) ----------
class PaymentReceiptOut(BaseModel):
    """A recorded client payment, enriched with its invoice number / client."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    invoice_number: str
    client_id: int
    amount: float
    payment_date: date
    payment_mode: PaymentMode
    bank_reference: str | None
    tds_deducted: float
    gst_component: float
    remarks: str | None


# ---------- Verification ----------
class BankTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    statement_id: int
    txn_date: date | None
    amount: float
    utr_reference: str | None
    narration: str | None
    counterparty: str | None
    verification_status: VerificationStatus
    matched_payment_id: int | None
    matched_approval_id: int | None
    match_note: str | None


class BankStatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_name: str
    bank_name: str | None
    account_number: str | None
    uploaded_by: str | None
    transaction_count: int
    matched_count: int
    created_at: datetime


class BankStatementDetail(BankStatementOut):
    transactions: list[BankTransactionOut] = []


# ---------- Audit ----------
class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_name: str | None
    actor_role: str | None
    action: str
    entity_type: str
    entity_id: int | None
    detail: str | None
    created_at: datetime


# ---------- Dashboard ----------
class CashflowPoint(BaseModel):
    """One bucket of real received revenue (a week / month / year)."""

    label: str
    value: float


class DashboardSummary(BaseModel):
    total_clients: int
    total_vendors: int
    total_invoices: int
    net_receivable: float
    net_payable: float
    pending_approvals: int
    gst_pending: float
    reconciliation_rate: float
    recent_invoices: list[dict]
    approvals_queue: list[dict]
    cashflow: list[dict]
