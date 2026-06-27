from datetime import date, datetime

from pydantic import BaseModel, EmailStr, ConfigDict

from app.models.enums import VendorApprovalStatus, AllocationStatus, InvoiceStatus


class VendorBase(BaseModel):
    business_name: str
    legal_name: str | None = None
    contact_person: str | None = None
    email: EmailStr
    phone: str | None = None
    address: str | None = None
    gst_number: str | None = None
    pan: str | None = None
    bank_account_holder: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    ifsc_code: str | None = None
    tax_applicable: bool = True
    compliance_documents: str | None = None
    annual_service_contract: str | None = None


class VendorCreate(VendorBase):
    pass


class VendorUpdate(BaseModel):
    business_name: str | None = None
    contact_person: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None
    gst_number: str | None = None
    pan: str | None = None
    bank_account_holder: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    ifsc_code: str | None = None
    tax_applicable: bool | None = None
    approval_status: VendorApprovalStatus | None = None


class VendorOut(VendorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
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


class VendorInvoiceCreate(BaseModel):
    vendor_id: int
    allocation_id: int | None = None
    invoice_number: str
    invoice_date: date
    invoice_amount: float = 0.0
    gst_amount: float = 0.0
    tds_applicable: bool = True
    tds_rate: float = 2.0


class VendorInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int
    allocation_id: int | None
    invoice_number: str
    invoice_date: date
    invoice_amount: float
    gst_amount: float
    tds_applicable: bool
    tds_rate: float
    tds_amount: float
    net_payable: float
    report_submitted: bool
    status: InvoiceStatus
    created_at: datetime
