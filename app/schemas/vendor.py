from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, StringConstraints

from app.models.enums import VendorApprovalStatus, AllocationStatus, InvoiceStatus
from app.services.taxation import DEFAULT_VENDOR_TDS_SECTION, TDS_SECTIONS, VENDOR_GST_RATES

PAN_PATTERN = r"^[A-Z]{5}[0-9]{4}[A-Z]$"
UDYAM_PATTERN = r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$"
IFSC_PATTERN = r"^[A-Z]{4}0[A-Z0-9]{6}$"
ACCOUNT_NUMBER_PATTERN = r"^[0-9]{9,18}$"


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _normalize_code(value: object) -> object:
    """Identifiers are typed by people: trim and upper-case them before the format check."""
    value = _blank_to_none(value)
    return value.strip().upper() if isinstance(value, str) else value


def _code(pattern: str, max_length: int):
    return Annotated[
        str,
        BeforeValidator(_normalize_code),
        StringConstraints(pattern=pattern, max_length=max_length),
    ]


OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]
Pan = Annotated[_code(PAN_PATTERN, 10) | None, BeforeValidator(_blank_to_none)]
UdyamNumber = Annotated[_code(UDYAM_PATTERN, 30) | None, BeforeValidator(_blank_to_none)]
Coi = Annotated[
    Annotated[str, StringConstraints(max_length=120)] | None,
    BeforeValidator(_normalize_code),
]
RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class VendorBankAccountIn(BaseModel):
    account_holder: RequiredText
    bank_name: RequiredText
    account_number: Annotated[str, StringConstraints(strip_whitespace=True, pattern=ACCOUNT_NUMBER_PATTERN)]
    ifsc_code: _code(IFSC_PATTERN, 11)


class VendorBankAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_holder: str
    bank_name: str
    account_number: str
    ifsc_code: str


class VendorCreate(BaseModel):
    business_name: RequiredText
    email: EmailStr
    phone: OptionalText = None
    gst_number: OptionalText = None
    msme_number: UdyamNumber = None
    coi: Coi = None
    pan: Pan = None
    bank_accounts: list[VendorBankAccountIn] = []


class VendorUpdate(BaseModel):
    business_name: RequiredText | None = None
    email: EmailStr | None = None
    phone: OptionalText = None
    gst_number: OptionalText = None
    msme_number: UdyamNumber = None
    coi: Coi = None
    pan: Pan = None
    # When sent, replaces the vendor's bank accounts. Leave out to keep them.
    bank_accounts: list[VendorBankAccountIn] | None = None
    approval_status: VendorApprovalStatus | None = None


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
    project_name: str
    scope_of_work: str | None = None
    agreed_cost: float = 0.0
    vendor_margin: float = 0.0
    allocation_percent: float = 100.0
    start_date: date | None = None
    end_date: date | None = None
    expected_report_date: date | None = None
    internal_owner: str | None = None
    status: AllocationStatus = AllocationStatus.NOT_STARTED


class AllocationOut(AllocationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int


def _gst_slab(rate: float) -> float:
    if rate not in VENDOR_GST_RATES:
        raise ValueError(f"GST rate must be one of {', '.join(f'{r:g}%' for r in VENDOR_GST_RATES)}")
    return rate


TdsSectionKey = Literal[tuple(TDS_SECTIONS)]  # type: ignore[valid-type]


class VendorInvoiceCreate(BaseModel):
    """A vendor's bill. GST and TDS are worked out from the rate, the state and the TDS section."""

    vendor_id: int
    project_id: int | None = None
    invoice_number: str
    invoice_date: date
    invoice_amount: float = Field(default=0.0, ge=0)  # before GST
    gst_rate: Annotated[float, AfterValidator(_gst_slab)] = 18.0
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
