from datetime import date, datetime

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.services.taxation import DEFAULT_CLIENT_TDS_RATE
from app.models.enums import ClientApprovalMethod, InvoiceLinkPurpose, InvoiceStatus, PaymentMode, GstStatus

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
BankName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class InvoiceCreate(BaseModel):
    """Every new invoice is a proforma draft. Numbers, dates and stages are set by the server."""

    client_id: int
    agent_id: int | None = None
    service_id: int
    due_date: date | None = None
    # Left empty, these come from the chosen service.
    service_description: str | None = None
    sac_code: str | None = None
    taxable_value: float = 0.0
    gst_rate: float | None = None
    is_interstate: bool = False
    tds_rate: float = DEFAULT_CLIENT_TDS_RATE
    supporting_document: str | None = None
    internal_remarks: str | None = None


class InvoiceUpdate(BaseModel):
    client_id: int | None = None
    agent_id: int | None = None
    service_id: int | None = None
    due_date: date | None = None
    service_description: str | None = None
    sac_code: str | None = None
    taxable_value: float | None = None
    gst_rate: float | None = None
    is_interstate: bool | None = None
    tds_rate: float | None = None
    supporting_document: str | None = None
    internal_remarks: str | None = None
    status: InvoiceStatus | None = None


class PaymentCreate(BaseModel):
    """A client payment. It is always received by bank, so the mode is not chosen."""

    amount: float
    payment_date: date
    bank_name: BankName
    bank_reference: str | None = None
    tds_deducted: float = Field(default=0.0, ge=0)
    gst_component: float = 0.0
    remarks: str | None = None
    attachment: str | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    amount: float
    payment_date: date
    payment_mode: PaymentMode
    bank_name: str | None
    bank_reference: str | None
    tds_deducted: float
    gst_component: float
    remarks: str | None
    attachment: str | None


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str | None
    proforma_number: str | None
    display_number: str
    is_issued: bool
    client_id: int
    agent_id: int | None
    invoice_date: date | None
    proforma_date: date | None
    due_date: date | None
    service_id: int | None
    service_title: str | None
    service_description: str | None
    sac_code: str | None
    taxable_value: float
    gst_rate: float
    gst_amount: float
    cgst: float
    sgst: float
    igst: float
    is_interstate: bool
    tds_applicable: bool
    tds_rate: float
    expected_tds: float
    total_amount: float
    amount_received: float
    amount_pending: float
    status: InvoiceStatus
    gst_status: GstStatus
    is_locked: bool
    locked_at: datetime | None
    supporting_document: str | None
    internal_remarks: str | None
    created_by_id: int | None
    client_approved_at: datetime | None
    client_approval_method: ClientApprovalMethod | None
    client_approver_name: str | None
    client_approval_note: str | None
    change_request_note: str | None
    change_requested_by: str | None
    issued_by_id: int | None
    issued_at: datetime | None
    created_at: datetime


class InvoiceDetail(InvoiceOut):
    payments: list[PaymentOut] = []


# ---------- Approval workflow ----------


class WorkflowResult(BaseModel):
    invoice: InvoiceDetail
    link_url: str | None = None  # shown to the team so they can share it if email is not configured
    emailed: bool = False


class ManualApprovalIn(BaseModel):
    note: LongText


class ReturnIn(BaseModel):
    reason: LongText


class TimelineEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor_name: str | None
    actor_role: str | None
    detail: str | None
    created_at: datetime


# ---------- Public (client-facing) ----------


class ClientApproveIn(BaseModel):
    name: ShortText


class ClientChangesIn(BaseModel):
    comment: LongText


class PublicCompany(BaseModel):
    legal_name: str
    address: str
    gstin: str
    state: str
    pan: str
    email: str
    bank_name: str
    bank_account: str
    bank_ifsc: str


class PublicClient(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_name: str
    legal_name: str | None
    billing_address: str | None
    gst_number: str | None


class PublicInvoice(BaseModel):
    """What a client may see. Deliberately excludes internal remarks, TDS and agent details."""

    model_config = ConfigDict(from_attributes=True)

    display_number: str
    is_issued: bool
    proforma_number: str | None
    invoice_number: str | None
    proforma_date: date | None
    invoice_date: date | None
    due_date: date | None
    service_title: str | None
    service_description: str | None
    sac_code: str | None
    taxable_value: float
    gst_rate: float
    gst_amount: float
    cgst: float
    sgst: float
    igst: float
    is_interstate: bool
    total_amount: float
    status: InvoiceStatus
    client_approved_at: datetime | None
    client_approver_name: str | None


class PublicInvoiceView(BaseModel):
    purpose: InvoiceLinkPurpose
    can_act: bool
    invoice: PublicInvoice
    client: PublicClient
    company: PublicCompany


class BulkRowOut(BaseModel):
    """One spreadsheet row of a bulk upload, checked but not yet created."""

    row: int
    client_input: str
    client_name: str | None
    service_input: str
    service_title: str | None
    amount: float | None
    gst_rate: float | None
    gst_amount: float | None
    total_amount: float | None
    description: str | None
    due_date: date | None
    tds_rate: float
    is_interstate: bool
    errors: list[str]
    warnings: list[str]


class BulkPreviewOut(BaseModel):
    rows: list[BulkRowOut]
    ready: bool  # true only when no row has an error
    invoice_count: int
    error_count: int  # rows with at least one error
    client_count: int
    taxable_total: float
    gst_total: float
    grand_total: float


class BulkCreatedInvoice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proforma_number: str
    client_id: int
    total_amount: float


class BulkCreatedOut(BaseModel):
    created: int
    invoices: list[BulkCreatedInvoice]
