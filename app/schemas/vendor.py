from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, model_validator

from app.models.enums import VendorApprovalStatus, AllocationStatus, InvoiceStatus
from app.schemas.fields import (
    AccountNumber,
    GstRate,
    Ifsc,
    InvoiceNumber,
    Money,
    Name,
    OptionalCin,
    OptionalGstin,
    OptionalName,
    OptionalPan,
    OptionalPhone,
    OptionalText,
    OptionalUdyam,
    PastDate,
    Percent,
    PositiveMoney,
    check_date_order,
    check_pan_matches_gstin,
)
from app.services.taxation import DEFAULT_VENDOR_TDS_SECTION, TDS_SECTIONS


class VendorBankAccountIn(BaseModel):
    account_holder: Name
    bank_name: Name
    account_number: AccountNumber
    ifsc_code: Ifsc


class VendorBankAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_holder: str
    bank_name: str
    account_number: str
    ifsc_code: str


def _no_repeated_accounts(accounts: list[VendorBankAccountIn]) -> None:
    numbers = [(a.ifsc_code, a.account_number) for a in accounts]
    if len(numbers) != len(set(numbers)):
        raise ValueError("The same bank account is listed twice.")


class VendorCreate(BaseModel):
    business_name: Name
    email: EmailStr
    phone: OptionalPhone = None
    gst_number: OptionalGstin = None
    msme_number: OptionalUdyam = None
    coi: OptionalCin = None
    pan: OptionalPan = None
    bank_accounts: list[VendorBankAccountIn] = []

    @model_validator(mode="after")
    def _consistent(self):
        check_pan_matches_gstin(self.pan, self.gst_number)
        _no_repeated_accounts(self.bank_accounts)
        return self


class VendorUpdate(BaseModel):
    business_name: Name | None = None
    email: EmailStr | None = None
    phone: OptionalPhone = None
    gst_number: OptionalGstin = None
    msme_number: OptionalUdyam = None
    coi: OptionalCin = None
    pan: OptionalPan = None
    # When sent, replaces the vendor's bank accounts. Leave out to keep them.
    bank_accounts: list[VendorBankAccountIn] | None = None
    approval_status: VendorApprovalStatus | None = None

    @model_validator(mode="after")
    def _consistent(self):
        check_pan_matches_gstin(self.pan, self.gst_number)
        _no_repeated_accounts(self.bank_accounts or [])
        return self


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    business_name: str
    legal_name: str | None
    contact_person: str | None
    email: str
    phone: str | None
    address: str | None
    gst_number: str | None
    msme_number: str | None
    coi: str | None
    pan: str | None
    bank_accounts: list[VendorBankAccountOut]
    tax_applicable: bool
    compliance_documents: str | None
    annual_service_contract: str | None
    approval_status: VendorApprovalStatus
    is_verified: bool
    created_at: datetime


class AllocationCreate(BaseModel):
    vendor_id: int
    client_id: int | None = None
    project_name: Name
    scope_of_work: OptionalText = None
    agreed_cost: Money = 0.0
    vendor_margin: Money = 0.0
    allocation_percent: Percent = 100.0
    start_date: date | None = None
    end_date: date | None = None
    expected_report_date: date | None = None
    internal_owner: OptionalName = None
    status: AllocationStatus = AllocationStatus.NOT_STARTED

    @model_validator(mode="after")
    def _dates_in_order(self):
        check_date_order(self.start_date, self.end_date, "the start date", "The end date")
        return self


class AllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int
    client_id: int | None
    project_name: str
    scope_of_work: str | None
    agreed_cost: float
    vendor_margin: float
    allocation_percent: float
    start_date: date | None
    end_date: date | None
    expected_report_date: date | None
    internal_owner: str | None
    status: AllocationStatus


TdsSectionKey = Literal[tuple(TDS_SECTIONS)]  # type: ignore[valid-type]


class VendorInvoiceCreate(BaseModel):
    """A vendor's bill. GST and TDS are worked out from the rate, the state and the TDS section."""

    vendor_id: int
    project_id: int | None = None
    invoice_number: InvoiceNumber
    invoice_date: PastDate
    invoice_amount: PositiveMoney  # before GST
    gst_rate: GstRate = 18.0
    is_interstate: bool = False
    tds_section: TdsSectionKey = DEFAULT_VENDOR_TDS_SECTION


class VendorInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int
    project_id: int | None
    invoice_number: str
    invoice_date: date
    invoice_amount: float
    gst_amount: float
    gst_rate: float | None
    is_interstate: bool
    cgst: float | None
    sgst: float | None
    igst: float | None
    vendor_gstin: str | None
    tds_applicable: bool
    tds_section: str | None
    tds_rate: float
    tds_amount: float
    tds_deposited_on: date | None
    tds_challan_number: str | None
    net_payable: float
    report_submitted: bool
    status: InvoiceStatus
    created_at: datetime
