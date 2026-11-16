from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import AllocationStatus, InvoiceStatus
from app.schemas.fields import Money, OptionalName, OptionalText

CODE_PATTERN = r"^[A-Z0-9][A-Z0-9/_.-]{0,39}$"


def _normalize_code(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


ProjectCode = Annotated[str, BeforeValidator(_normalize_code), StringConstraints(pattern=CODE_PATTERN)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class AssignedServiceIn(BaseModel):
    """One service a vendor delivers on the project, at an agreed cost before GST."""

    service_id: int
    agreed_cost: Money


class ProjectVendorServicesIn(BaseModel):
    """The services a vendor delivers on a project. Sending it replaces the vendor's list."""

    services: list[AssignedServiceIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _each_service_once(self):
        ids = [s.service_id for s in self.services]
        if len(ids) != len(set(ids)):
            raise ValueError("A service is listed twice for this vendor.")
        return self


class ProjectVendorIn(ProjectVendorServicesIn):
    vendor_id: int


def _service_ids(service_ids: list[int] | None, service_id: int | None) -> list[int] | None:
    """``service_ids``, or the single ``service_id`` older callers send."""
    if service_ids is not None:
        return service_ids
    return [service_id] if service_id is not None else None


class ProjectCreate(BaseModel):
    # Left empty, the next PRJ-0001 style code.
    code: ProjectCode | None = None
    title: Title
    brand: OptionalName = None
    description: OptionalText = None
    client_id: int
    # The services billed; each becomes a line, with its SAC code and GST rate, on the project's invoices.
    service_ids: list[int] | None = None
    service_id: int | None = None
    budget: Money = 0.0
    contract_value: Money = 0.0
    # The start month (any day in it is kept as the 1st).
    start_date: date | None = None
    end_date: date | None = None
    # Left empty, the 25th of the month after the start month.
    expected_report_date: date | None = None
    status: AllocationStatus = AllocationStatus.NOT_STARTED
    vendors: list[ProjectVendorIn] = []

    @model_validator(mode="after")
    def _needs_a_service(self):
        self.service_ids = _service_ids(self.service_ids, self.service_id)
        if not self.service_ids:
            raise ValueError("Choose at least one service.")
        return self


class ProjectUpdate(BaseModel):
    code: ProjectCode | None = None
    title: Title | None = None
    brand: OptionalName = None
    description: OptionalText = None
    client_id: int | None = None
    # When sent, replaces the project's services.
    service_ids: list[int] | None = None
    service_id: int | None = None
    budget: Money | None = None
    contract_value: Money | None = None
    start_date: date | None = None
    end_date: date | None = None
    expected_report_date: date | None = None
    status: AllocationStatus | None = None
    # When sent, replaces the project's vendors. Leave out to keep them.
    vendors: list[ProjectVendorIn] | None = None

    @model_validator(mode="after")
    def _one_list(self):
        self.service_ids = _service_ids(self.service_ids, self.service_id)
        if self.service_ids is not None and not self.service_ids:
            raise ValueError("Choose at least one service.")
        return self


class AssignedServiceOut(BaseModel):
    """A vendor's service on a project, under its permanent code."""

    id: int
    code: str
    service_id: int
    service_title: str
    sac_code: str | None
    agreed_cost: float
    invoiced: float  # charged against this code on vendor bills, before GST
    over_invoiced: bool


class ProjectVendorOut(BaseModel):
    id: int
    vendor_id: int
    vendor_name: str
    vendor_email: str
    is_group_company: bool = False
    services: list[AssignedServiceOut]
    agreed_cost: float  # the services' agreed costs together
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


class ProjectClientInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_number: str
    proforma_date: date | None
    invoice_date: date | None
    taxable_value: float
    total_amount: float
    status: InvoiceStatus


class ProjectServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    sac_code: str | None
    gst_rate: float
    is_active: bool


class ProjectOut(BaseModel):
    id: int
    code: str
    title: str
    brand: str | None
    description: str | None
    client_id: int | None
    client_name: str | None
    # Every service billed; service_id, service_title, sac_code and gst_rate describe the first.
    services: list[ProjectServiceOut]
    service_id: int | None
    service_title: str | None
    sac_code: str | None
    gst_rate: float | None
    budget: float
    start_date: date | None
    end_date: date | None
    expected_report_date: date | None
    status: AllocationStatus
    vendors: list[ProjectVendorOut]
    committed_cost: float  # sum of the vendors' agreed costs
    invoiced_cost: float
    remaining_budget: float
    over_budget: bool
    # Billing to the client: taxable value of its invoices that are not cancelled.
    contract_value: float
    billed: float
    left_to_bill: float | None  # None when no contract value is set
    over_contract: bool
    margin: float  # billed minus what vendors have invoiced
    # Per service the project bills: client billing against vendor cost.
    service_margins: list["ServiceMarginOut"] = []
    created_at: datetime


class ServiceMarginOut(BaseModel):
    """One project service: what the client is billed for it and what vendors cost for it."""

    service_id: int
    service_title: str
    billed: float  # client invoice lines for this service, before GST, cancelled excluded
    vendor_committed: float  # agreed with vendors for this service
    vendor_invoiced: float  # charged by vendors against this service's codes
    margin: float  # billed - vendor_invoiced


class ProjectDetail(ProjectOut):
    vendor_invoices: list[ProjectVendorInvoiceOut]
    client_invoices: list[ProjectClientInvoiceOut]


class WorkOrderSent(BaseModel):
    work_order_number: str
    to_email: str
    emailed: bool


class VendorAssignmentOut(BaseModel):
    """A service a vendor is assigned on some project: what a vendor bill can charge against."""

    id: int
    code: str
    project_id: int
    project_code: str
    project_title: str
    service_id: int
    service_title: str
    sac_code: str | None
    agreed_cost: float
    invoiced: float
    remaining: float  # agreed_cost - invoiced (negative when over-billed)
    # "Not invoiced", "Partly invoiced", "Fully invoiced" or "Over-invoiced".
    invoice_status: str
    # Released to the vendor against this code's bill lines (after commission).
    paid: float = 0.0
