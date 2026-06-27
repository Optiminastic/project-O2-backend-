from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import InvoiceStatus, PaymentMode, GstStatus


class InvoiceCreate(BaseModel):
    invoice_number: str | None = None  # auto-generated if omitted
    client_id: int
    invoice_date: date
    due_date: date | None = None
    service_description: str | None = None
    taxable_value: float = 0.0
    gst_rate: float = 18.0
    is_interstate: bool = False
    tds_applicable: bool = False
    tds_rate: float = 0.0
    internal_remarks: str | None = None
    status: InvoiceStatus = InvoiceStatus.DRAFT


class InvoiceUpdate(BaseModel):
    client_id: int | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    service_description: str | None = None
    taxable_value: float | None = None
    gst_rate: float | None = None
    is_interstate: bool | None = None
    tds_applicable: bool | None = None
    tds_rate: float | None = None
    internal_remarks: str | None = None
    status: InvoiceStatus | None = None


class PaymentCreate(BaseModel):
    amount: float
    payment_date: date
    bank_reference: str | None = None
    payment_mode: PaymentMode = PaymentMode.NEFT
    tds_deducted: float = 0.0
    gst_component: float = 0.0
    remarks: str | None = None


class PaymentOut(PaymentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    client_id: int
    invoice_date: date
    due_date: date | None
    service_description: str | None
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
    internal_remarks: str | None
    created_at: datetime


class InvoiceDetail(InvoiceOut):
    payments: list[PaymentOut] = []
