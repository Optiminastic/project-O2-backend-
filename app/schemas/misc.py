from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, model_validator

from app.models.enums import ReportReviewStatus, ApprovalStatus, VerificationStatus, PaymentMode
from app.schemas.fields import (
    BankReference,
    Money,
    Name,
    OptionalName,
    OptionalText,
    PastDate,
    PositiveMoney,
)


# ---------- Reports ----------
class ReportCreate(BaseModel):
    vendor_id: int | None = None
    client_id: int | None = None
    allocation_id: int | None = None
    project_name: Name
    reporting_period: OptionalName = None
    report_type: OptionalName = None
    uploaded_file: OptionalText = None
    submission_date: PastDate | None = None
    internal_reviewer: OptionalName = None
    remarks: OptionalText = None


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int | None
    client_id: int | None
    allocation_id: int | None
    project_name: str
    reporting_period: str | None
    report_type: str | None
    uploaded_file: str | None
    submission_date: date | None
    internal_reviewer: str | None
    remarks: str | None
    review_status: ReportReviewStatus
    created_at: datetime


class EmailReportRequest(BaseModel):
    to_email: EmailStr | None = None  # defaults to client email
    subject: OptionalName = None
    body: OptionalText = None


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
    payee_name: Name
    amount: PositiveMoney
    purpose: OptionalText = None
    vendor_invoice_id: int | None = None
    tax_deductions: Money = 0.0
    # Left at 0, worked out as the amount less tax deductions.
    net_payable: Money = 0.0
    bank_details: OptionalText = None

    @model_validator(mode="after")
    def _within_amount(self):
        if self.tax_deductions > self.amount:
            raise ValueError("Tax deductions cannot be more than the amount.")
        if self.net_payable > self.amount:
            raise ValueError("Net payable cannot be more than the amount.")
        return self


class ApprovalDecision(BaseModel):
    approve: bool
    comment: OptionalText = None

    @model_validator(mode="after")
    def _reason_for_rejection(self):
        if not self.approve and not self.comment:
            raise ValueError("Give a reason when rejecting a payment.")
        return self


class PaymentRelease(BaseModel):
    payment_reference: BankReference


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
    bank_name: str | None
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


# ---------- GST lookup ----------
class GstLookupOut(BaseModel):
    """Result of verifying/looking up a GSTIN.

    `valid` reflects the format + checksum check (always computed offline).
    `pan` and `state` are derived from the number itself. `legal_name`,
    `trade_name` and `address` are only populated when a live provider key is
    configured and the lookup succeeds. `source` is "appyflow" or "offline".
    """

    gstin: str
    valid: bool
    pan: str | None = None
    state: str | None = None
    state_code: str | None = None
    legal_name: str | None = None
    trade_name: str | None = None
    address: str | None = None
    status: str | None = None  # registration status, e.g. "Active"
    source: str = "offline"
    message: str | None = None


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
    gst_payable: float  # output GST less input credit, never negative
    reconciliation_rate: float
    recent_invoices: list[dict]
    approvals_queue: list[dict]
    cashflow: list[dict]
