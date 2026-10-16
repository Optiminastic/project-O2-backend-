from datetime import date, datetime

from typing import Annotated

from pydantic import AfterValidator, AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.schemas.fields import (
    LongText,
    Money,
    Name,
    OptionalBankReference,
    OptionalText,
    PastDate,
    PositiveMoney,
    TdsRate,
)
from app.services.numbering import business_date
from app.services.taxation import DEFAULT_CLIENT_TDS_RATE
from app.models.enums import ClientApprovalMethod, InvoiceLinkPurpose, InvoiceStatus, PaymentMode, GstStatus


def _not_past(value: date | None) -> date | None:
    if value and value < business_date():
        raise ValueError("Due date cannot be in the past.")
    return value


DueDate = Annotated[date | None, AfterValidator(_not_past)]

# One line per service; an invoice bills at most this many.
MAX_LINES = 50


class InvoiceLineIn(BaseModel):
    """One of the project's services and the amount billed for it (before GST)."""

    service_id: int
    taxable_value: PositiveMoney
    # Left empty, the service title is printed.
    description: OptionalText = None


Lines = Annotated[list[InvoiceLineIn], Field(min_length=1, max_length=MAX_LINES)]


class InvoiceCreate(BaseModel):
    """Every new invoice is a proforma draft. Numbers and stages are set by the server.

    Bill the project's services with ``lines``, or send one ``taxable_value`` to bill
    its first service.
    """

    # The project billed: the client and the services (with SAC code and GST rate) come from it.
    project_id: int
    # The proforma date: today, or backdated within the current month.
    proforma_date: date | None = None
    agent_id: int | None = None
    due_date: DueDate = None
    lines: Lines | None = None
    taxable_value: PositiveMoney | None = None
    service_description: OptionalText = None
    # Decided by the client's GSTIN state when it has one; this choice counts only without a GSTIN.
    is_interstate: bool = False
    tds_rate: TdsRate = DEFAULT_CLIENT_TDS_RATE
    supporting_document: OptionalText = None
    internal_remarks: OptionalText = None

    @model_validator(mode="after")
    def _amount_given(self):
        if self.lines is None and self.taxable_value is None:
            raise ValueError("Enter the amount for at least one service.")
        return self


class InvoiceUpdate(BaseModel):
    # Moving a proforma to another project re-copies its client and service details.
    project_id: int | None = None
    proforma_date: date | None = None
    agent_id: int | None = None
    # Not checked against today: an overdue invoice keeps its original due date when edited.
    due_date: date | None = None
    # When sent, replaces the invoice's lines.
    lines: Lines | None = None
    # Single-amount edit, for an invoice with one line.
    taxable_value: PositiveMoney | None = None
    service_description: OptionalText = None
    is_interstate: bool | None = None
    tds_rate: TdsRate | None = None
    supporting_document: OptionalText = None
    internal_remarks: OptionalText = None
    status: InvoiceStatus | None = None


class PaymentCreate(BaseModel):
    """A client payment. It is always received by bank, so the mode is not chosen.

    Name the bank with ``bank_account_id`` (one of ours, from the Banks page), or
    with a typed ``bank_name``.
    """

    amount: PositiveMoney
    payment_date: PastDate
    bank_account_id: int | None = None
    bank_name: Name | None = None
    bank_reference: OptionalBankReference = None
    # Left out, the invoice's TDS is counted as deducted once the cash clears the rest.
    tds_deducted: Money | None = None
    gst_component: Money = 0.0
    remarks: OptionalText = None
    attachment: OptionalText = None

    @model_validator(mode="after")
    def _gst_within_amount(self):
        if self.bank_account_id is None and not self.bank_name:
            raise ValueError("Choose the bank the money was received into.")
        if self.gst_component > self.amount:
            raise ValueError("The GST component cannot be more than the amount received.")
        return self


MAX_RECEIPT_INVOICES = 50


class ReceiptAllocation(BaseModel):
    """The part of one receipt that pays one invoice."""

    invoice_id: int
    amount: PositiveMoney
    gst_component: Money = 0.0


class MultiReceiptCreate(BaseModel):
    """One bank receipt paying several invoices of the same client. TDS is counted per invoice, as on one receipt."""

    payment_date: PastDate
    bank_account_id: int | None = None
    bank_name: Name | None = None
    bank_reference: OptionalBankReference = None
    remarks: OptionalText = None
    attachment: OptionalText = None
    allocations: list[ReceiptAllocation] = Field(min_length=1, max_length=MAX_RECEIPT_INVOICES)

    @model_validator(mode="after")
    def _one_line_per_invoice(self):
        if self.bank_account_id is None and not self.bank_name:
            raise ValueError("Choose the bank the money was received into.")
        ids = [a.invoice_id for a in self.allocations]
        if len(ids) != len(set(ids)):
            raise ValueError("Each invoice can appear only once on a receipt.")
        return self


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    amount: float
    payment_date: date
    payment_mode: PaymentMode
    bank_name: str | None
    bank_account_id: int | None
    bank_reference: str | None
    tds_deducted: float
    gst_component: float
    remarks: str | None
    attachment: str | None
    # Shared by the rows of one receipt that paid several invoices.
    receipt_ref: str | None = None


class MultiReceiptOut(BaseModel):
    receipt_ref: str
    total: float
    payments: list[PaymentOut]


class InvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_id: int | None
    service_title: str
    description: str | None
    sac_code: str | None
    gst_rate: float
    taxable_value: float
    gst_amount: float
    cgst: float
    sgst: float
    igst: float


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str | None
    proforma_number: str | None
    display_number: str
    is_issued: bool
    project_id: int | None
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
    lines: list[InvoiceLineOut] = Field(validation_alias=AliasChoices("line_items", "lines"))


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
    name: Name


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
    bank_branch: str


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
    lines: list[InvoiceLineOut] = Field(validation_alias=AliasChoices("line_items", "lines"))


class InvoiceDocumentView(BaseModel):
    """Everything printed on the proforma / tax invoice document."""

    invoice: PublicInvoice
    client: PublicClient
    company: PublicCompany
    place_of_supply: str | None
    amount_in_words: str


class PublicInvoiceView(InvoiceDocumentView):
    purpose: InvoiceLinkPurpose
    can_act: bool


class BulkRowOut(BaseModel):
    """One spreadsheet row of a bulk upload, checked but not yet created."""

    row: int
    project_input: str
    project_code: str | None
    client_name: str | None
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
