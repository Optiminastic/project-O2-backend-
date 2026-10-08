from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints

from app.models.enums import AllocationStatus

CODE_PATTERN = r"^[A-Z0-9][A-Z0-9/_.-]{0,39}$"


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _normalize_code(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


ProjectCode = Annotated[str, BeforeValidator(_normalize_code), StringConstraints(pattern=CODE_PATTERN)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]
Money = Annotated[float, Field(ge=0)]
Percent = Annotated[float, Field(gt=0, le=100)]


class ProjectVendorIn(BaseModel):
    vendor_id: int
    work_percent: Percent
    agreed_cost: Money


class ProjectCreate(BaseModel):
    code: ProjectCode
    title: Title
    brand: OptionalText = None
    description: OptionalText = None
    client_id: int
    budget: Money = 0.0
    start_date: date | None = None
    end_date: date | None = None
    # Left empty, the last day of the end date's month.
    expected_report_date: date | None = None
    status: AllocationStatus = AllocationStatus.NOT_STARTED
    vendors: list[ProjectVendorIn] = []


class ProjectUpdate(BaseModel):
    code: ProjectCode | None = None
    title: Title | None = None
    brand: OptionalText = None
    description: OptionalText = None
    client_id: int | None = None
    budget: Money | None = None
    start_date: date | None = None
    end_date: date | None = None
    expected_report_date: date | None = None
    status: AllocationStatus | None = None
    # When sent, replaces the project's vendors. Leave out to keep them.
    vendors: list[ProjectVendorIn] | None = None


class ProjectVendorOut(BaseModel):
    id: int
    vendor_id: int
    vendor_name: str
    vendor_email: str
    work_percent: float
    agreed_cost: float
    invoiced: float  # vendor invoices recorded against this project, before GST
    over_invoiced: bool
    work_order_number: str | None
    work_order_date: date | None
    work_order_sent_at: datetime | None
    work_order_outdated: bool


class ProjectVendorInvoiceOut(BaseModel):
    id: int
    vendor_id: int
    vendor_name: str
    invoice_number: str
    invoice_date: date
    invoice_amount: float
    net_payable: float
    status: str


class ProjectOut(BaseModel):
    id: int
    code: str
    title: str
    brand: str | None
    description: str | None
    client_id: int | None
    client_name: str | None
    budget: float
    start_date: date | None
    end_date: date | None
    expected_report_date: date | None
    status: AllocationStatus
    vendors: list[ProjectVendorOut]
    work_percent_assigned: float
    committed_cost: float  # sum of the vendors' agreed costs
    invoiced_cost: float
    remaining_budget: float
    over_budget: bool
    created_at: datetime


class ProjectDetail(ProjectOut):
    vendor_invoices: list[ProjectVendorInvoiceOut]


class WorkOrderSent(BaseModel):
    work_order_number: str
    to_email: str
    emailed: bool
