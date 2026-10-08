from datetime import date
from typing import Annotated

from pydantic import BaseModel, StringConstraints, model_validator

Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class GstTotals(BaseModel):
    taxable: float
    cgst: float
    sgst: float
    igst: float
    total: float


class TaxSummary(BaseModel):
    """The month's tax position: GST to pay after input credit, and TDS on both sides."""

    output_gst: GstTotals  # on our issued tax invoices
    input_gst: GstTotals  # on vendor bills we can claim as input credit
    input_gst_ineligible: float  # GST on bills from vendors without a GSTIN
    net_gst_payable: float
    gst_credit_carried: float  # input credit left over when it exceeds output GST
    sales_count: int
    purchase_count: int
    client_tds_deducted: float
    client_tds_certificates_pending: int
    vendor_tds_deducted: float
    vendor_tds_deposited: float
    vendor_tds_to_deposit: float
    vendor_tds_overdue_count: int


class SalesGstRow(BaseModel):
    invoice_id: int
    invoice_number: str
    invoice_date: date
    client_name: str
    client_gstin: str | None
    taxable_value: float
    gst_rate: float
    cgst: float
    sgst: float
    igst: float
    gst_amount: float
    total_amount: float


class PurchaseGstRow(BaseModel):
    vendor_invoice_id: int
    vendor_id: int
    invoice_number: str
    invoice_date: date
    vendor_name: str
    vendor_gstin: str | None
    taxable_value: float
    gst_rate: float | None  # None on bills recorded before the split existed
    cgst: float | None
    sgst: float | None
    igst: float | None
    gst_amount: float
    itc_eligible: bool


class ClientTdsRow(BaseModel):
    payment_id: int
    payment_date: date
    invoice_id: int
    invoice_number: str
    client_name: str
    tds_deducted: float
    certificate_number: str | None
    certificate_date: date | None


class VendorTdsRow(BaseModel):
    vendor_invoice_id: int
    vendor_id: int
    invoice_number: str
    invoice_date: date
    vendor_name: str
    vendor_pan: str | None
    section_label: str
    tds_rate: float
    tds_amount: float
    due_date: date
    deposited_on: date | None
    challan_number: str | None
    overdue: bool


def _both_or_neither(number: str | None, day: date | None, what: str) -> None:
    if (number is None) != (day is None):
        raise ValueError(f"Give both the {what} number and its date, or neither to clear it.")


class CertificateIn(BaseModel):
    certificate_number: Reference | None = None
    certificate_date: date | None = None

    @model_validator(mode="after")
    def _complete(self):
        _both_or_neither(self.certificate_number, self.certificate_date, "certificate")
        return self


class DepositIn(BaseModel):
    challan_number: Reference | None = None
    deposited_on: date | None = None

    @model_validator(mode="after")
    def _complete(self):
        _both_or_neither(self.challan_number, self.deposited_on, "challan")
        return self
