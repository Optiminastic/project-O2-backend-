"""Project rules: vendors and the services they deliver, budget use, report dates and work order numbers.

A vendor is put on a project for specific services. Each (project, vendor,
service) gets a permanent code, ``<project code>-V<nn>-S<nn>``, printed on the
work order; vendor bills charge against those codes.
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    ClientInvoice, InvoiceLine, InvoiceStatus, Project, ProjectVendor, ProjectVendorService, Service, Vendor,
    VendorInvoice, VendorInvoiceLine,
)
from app.schemas.project import (
    AssignedServiceIn, AssignedServiceOut, ProjectOut, ProjectServiceOut, ProjectVendorIn, ProjectVendorOut,
    ServiceMarginOut, VendorAssignmentOut,
)
from app.services.numbering import business_date, next_in_series, next_number

WORK_ORDER_PREFIX = "WO"
PROJECT_CODE_SERIES = "PRJ"
# Reports are due on this day of the month after the project starts.
REPORT_DUE_DAY = 25


def month_start(on: date | None) -> date | None:
    """A start month is kept as its 1st."""
    return on.replace(day=1) if on else None


def default_report_date(start: date | None) -> date:
    """The 25th of the month after the start month (or after this month, with no start)."""
    base = start or business_date()
    year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
    return date(year, month, REPORT_DUE_DAY)


def next_project_code(db: Session) -> str:
    """The next free PRJ-0001 style code."""
    while True:
        code = f"{PROJECT_CODE_SERIES}-{next_in_series(db, PROJECT_CODE_SERIES):04d}"
        if not db.query(Project.id).filter(func.lower(Project.code) == code.lower()).first():
            return code


def set_services(project: Project, services: list[Service]) -> None:
    """The project bills ``services``; the first is also kept in the older single-service column."""
    project.services = services
    project.service_id = services[0].id


def assert_dates(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The end date is before the start date.")


def assert_code_free(db: Session, code: str, exclude_id: int | None = None) -> None:
    q = db.query(Project).filter(func.lower(Project.code) == code.lower())
    if exclude_id is not None:
        q = q.filter(Project.id != exclude_id)
    if q.first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Another project already uses the code {code}.")


def checked_vendors(db: Session, shares: list[ProjectVendorIn]) -> dict[int, Vendor]:
    """The vendors named in ``shares``, after checking each appears once."""
    ids = [s.vendor_id for s in shares]
    if len(ids) != len(set(ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A vendor is listed twice. Give each vendor one row.")
    found = {v.id: v for v in db.query(Vendor).filter(Vendor.id.in_(ids)).all()} if ids else {}
    if len(found) != len(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor not found")
    return found


def service_code(project: Project, pv: ProjectVendor, seq: int) -> str:
    return f"{project.code}-V{pv.vendor_seq:02d}-S{seq:02d}"


def _invoiced_assignments(db: Session, assignment_ids: list[int]) -> dict[int, float]:
    """Amounts charged on vendor bills per assigned service."""
    if not assignment_ids:
        return {}
    rows = (
        db.query(VendorInvoiceLine.assignment_id, func.sum(VendorInvoiceLine.amount))
        .filter(VendorInvoiceLine.assignment_id.in_(assignment_ids))
        .group_by(VendorInvoiceLine.assignment_id)
        .all()
    )
    return {a: round(total or 0.0, 2) for a, total in rows}


def _assert_assignable(project: Project, vendor: Vendor, services: list[Service]) -> None:
    """A vendor delivers only services the project bills and the vendor provides."""
    on_project = {s.id for s in project.services}
    provided = {s.id for s in vendor.services}
    not_on_project = [s.title for s in services if s.id not in on_project]
    if not_on_project:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{', '.join(not_on_project)} is not a service on this project. Add it to the project first.",
        )
    not_provided = [s.title for s in services if s.id not in provided]
    if not_provided:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{vendor.business_name} does not provide {', '.join(not_provided)}. "
            "Add it to the vendor's services first.",
        )


def assign_vendor(db: Session, project: Project, vendor: Vendor, wanted: list[AssignedServiceIn]) -> ProjectVendor:
    """Put ``vendor`` on the project for exactly the ``wanted`` services, at their agreed costs.

    A service already assigned keeps its code; a new one gets the next code; a
    removed one goes, unless a vendor bill already charges it. Any change to an
    issued work order marks it to be sent again.
    """
    by_id = {s.id: s for s in db.query(Service).filter(Service.id.in_([w.service_id for w in wanted])).all()}
    if len(by_id) != len({w.service_id for w in wanted}):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    _assert_assignable(project, vendor, [by_id[w.service_id] for w in wanted])

    pv = next((x for x in project.vendors if x.vendor_id == vendor.id), None)
    if pv is None:
        project.last_vendor_seq = (project.last_vendor_seq or 0) + 1
        pv = ProjectVendor(vendor=vendor, vendor_id=vendor.id, vendor_seq=project.last_vendor_seq,
                           last_service_seq=0, work_percent=0.0)
        project.vendors.append(pv)

    costs = {w.service_id: w.agreed_cost for w in wanted}
    existing = {line.service_id: line for line in pv.services}
    removed = [line for sid, line in existing.items() if sid not in costs]
    charged = _invoiced_assignments(db, [line.id for line in removed if line.id])
    blocked = [line.service.title for line in removed if line.id in charged]
    if blocked:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{vendor.business_name} has already billed {', '.join(blocked)} on this project, so it cannot be removed.",
        )

    changed = bool(removed)
    lines = []
    for w in wanted:
        line = existing.get(w.service_id)
        if line is None:
            pv.last_service_seq = (pv.last_service_seq or 0) + 1
            line = ProjectVendorService(service=by_id[w.service_id], service_id=w.service_id,
                                        code=service_code(project, pv, pv.last_service_seq), agreed_cost=w.agreed_cost)
            changed = True
        elif line.agreed_cost != w.agreed_cost:
            line.agreed_cost = w.agreed_cost
            changed = True
        lines.append(line)
    pv.services = lines
    pv.agreed_cost = round(sum(line.agreed_cost for line in lines), 2)
    if changed and pv.work_order_number:
        pv.work_order_outdated = True
    return pv


def remove_vendor(db: Session, project: Project, pv: ProjectVendor) -> None:
    if project.id and (project.id, pv.vendor_id) in _invoiced(db, [project.id]):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{pv.vendor.business_name} already invoiced this project, so they cannot be removed from it.",
        )
    project.vendors.remove(pv)


def replace_vendors(db: Session, project: Project, shares: list[ProjectVendorIn]) -> None:
    """Make the project's vendors match ``shares``, keeping each kept vendor's codes and work order."""
    vendors = checked_vendors(db, shares)
    wanted = {s.vendor_id for s in shares}
    invoiced = _invoiced(db, [project.id]) if project.id else {}

    blocked = [pv.vendor.business_name for pv in project.vendors
               if pv.vendor_id not in wanted and (project.id, pv.vendor_id) in invoiced]
    if blocked:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{', '.join(blocked)} already invoiced this project, so they cannot be removed from it.",
        )
    project.vendors = [pv for pv in project.vendors if pv.vendor_id in wanted]
    for share in shares:
        assign_vendor(db, project, vendors[share.vendor_id], share.services)


def assert_services_not_assigned(project: Project, service_ids: list[int]) -> None:
    """A service cannot leave the project while a vendor is assigned to deliver it."""
    keep = set(service_ids)
    in_use = sorted({line.service.title for pv in project.vendors for line in pv.services if line.service_id not in keep})
    if in_use:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Vendors are assigned to {', '.join(in_use)} on this project. Remove those assignments first.",
        )


def ensure_work_order_number(db: Session, pv: ProjectVendor) -> None:
    if pv.work_order_number:
        return
    today = business_date()
    pv.work_order_number = next_number(db, WORK_ORDER_PREFIX, today)
    pv.work_order_date = today


def project_vendor(project: Project, vendor_id: int) -> ProjectVendor:
    for pv in project.vendors:
        if pv.vendor_id == vendor_id:
            return pv
    raise HTTPException(status.HTTP_404_NOT_FOUND, "This vendor is not on the project")


def billable_assignments(db: Session, vendor_id: int, assignment_ids: list[int]) -> list[ProjectVendorService]:
    """The vendor's assigned services a bill charges, all on one project, each once."""
    if len(assignment_ids) != len(set(assignment_ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A service code is listed twice on this bill.")
    rows = db.query(ProjectVendorService).filter(ProjectVendorService.id.in_(assignment_ids)).all()
    found = {r.id: r for r in rows}
    if len(found) != len(assignment_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service code not found")
    if any(r.project_vendor.vendor_id != vendor_id for r in rows):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A service code on this bill belongs to another vendor. Bill only this vendor's own codes.",
        )
    if len({r.project_vendor.project_id for r in rows}) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "One bill covers one project. Record a separate bill for each project."
        )
    return [found[i] for i in assignment_ids]


def invoice_status(agreed: float, invoiced: float) -> str:
    """Where a job (assigned service) stands on billing."""
    if invoiced <= 0:
        return "Not invoiced"
    if round(invoiced, 2) > round(agreed, 2):
        return "Over-invoiced"
    if round(invoiced, 2) == round(agreed, 2):
        return "Fully invoiced"
    return "Partly invoiced"


def _paid_assignments(db: Session, assignment_ids: list[int]) -> dict[int, float]:
    """Released vendor payouts per assigned service, through the bill lines they settled."""
    from app.models import ApprovalStatus, PaymentApproval, PayoutAllocation  # noqa: PLC0415 - avoid a cycle

    if not assignment_ids:
        return {}
    rows = (
        db.query(VendorInvoiceLine.assignment_id, func.sum(PayoutAllocation.amount))
        .join(PayoutAllocation, PayoutAllocation.line_id == VendorInvoiceLine.id)
        .join(PaymentApproval, PaymentApproval.id == PayoutAllocation.approval_id)
        .filter(VendorInvoiceLine.assignment_id.in_(assignment_ids),
                PaymentApproval.status.in_([ApprovalStatus.PAYMENT_RELEASED, ApprovalStatus.PAYMENT_VERIFIED]))
        .group_by(VendorInvoiceLine.assignment_id)
        .all()
    )
    return {a: round(total or 0.0, 2) for a, total in rows}


def vendor_assignments(db: Session, vendor_id: int) -> list[VendorAssignmentOut]:
    """Every service the vendor is assigned, newest project first, with what is billed so far."""
    rows = (
        db.query(ProjectVendorService)
        .join(ProjectVendor, ProjectVendor.id == ProjectVendorService.project_vendor_id)
        .join(Project, Project.id == ProjectVendor.project_id)
        .filter(ProjectVendor.vendor_id == vendor_id)
        .order_by(Project.created_at.desc(), ProjectVendorService.id)
        .all()
    )
    billed = _invoiced_assignments(db, [r.id for r in rows])
    paid = _paid_assignments(db, [r.id for r in rows])
    out = []
    for r in rows:
        project, spent = r.project_vendor.project, billed.get(r.id, 0.0)
        out.append(VendorAssignmentOut(
            id=r.id, code=r.code, project_id=project.id, project_code=project.code, project_title=project.title,
            service_id=r.service_id, service_title=r.service.title, sac_code=r.service.sac_code,
            agreed_cost=r.agreed_cost, invoiced=spent, remaining=round(r.agreed_cost - spent, 2),
            invoice_status=invoice_status(r.agreed_cost, spent), paid=paid.get(r.id, 0.0),
        ))
    return out


def has_vendor_invoices(db: Session, project_id: int) -> bool:
    return db.query(VendorInvoice.id).filter(VendorInvoice.project_id == project_id).first() is not None


def has_client_invoices(db: Session, project_id: int) -> bool:
    return db.query(ClientInvoice.id).filter(ClientInvoice.project_id == project_id).first() is not None


# ---------- read models ----------

def _invoiced(db: Session, project_ids: list[int]) -> dict[tuple[int, int], float]:
    """Vendor invoice totals (before GST) per (project, vendor), in one query."""
    if not project_ids:
        return {}
    rows = (
        db.query(VendorInvoice.project_id, VendorInvoice.vendor_id, func.sum(VendorInvoice.invoice_amount))
        .filter(VendorInvoice.project_id.in_(project_ids))
        .group_by(VendorInvoice.project_id, VendorInvoice.vendor_id)
        .all()
    )
    return {(p, v): round(total or 0.0, 2) for p, v, total in rows}


def _billed(db: Session, project_ids: list[int]) -> dict[int, float]:
    """Taxable value billed to the client per project (proformas included, cancelled excluded)."""
    if not project_ids:
        return {}
    rows = (
        db.query(ClientInvoice.project_id, func.sum(ClientInvoice.taxable_value))
        .filter(ClientInvoice.project_id.in_(project_ids), ClientInvoice.status != InvoiceStatus.CANCELLED)
        .group_by(ClientInvoice.project_id)
        .all()
    )
    return {p: round(total or 0.0, 2) for p, total in rows}


def _billed_by_service(db: Session, project_ids: list[int]) -> dict[tuple[int, int], float]:
    """Taxable value billed to the client per (project, service), cancelled invoices excluded."""
    if not project_ids:
        return {}
    rows = (
        db.query(ClientInvoice.project_id, InvoiceLine.service_id, func.sum(InvoiceLine.taxable_value))
        .join(InvoiceLine, InvoiceLine.invoice_id == ClientInvoice.id)
        .filter(ClientInvoice.project_id.in_(project_ids), ClientInvoice.status != InvoiceStatus.CANCELLED,
                InvoiceLine.service_id.isnot(None))
        .group_by(ClientInvoice.project_id, InvoiceLine.service_id)
        .all()
    )
    return {(p, s): round(total or 0.0, 2) for p, s, total in rows}


def project_views(db: Session, projects: list[Project]) -> list[ProjectOut]:
    ids = [p.id for p in projects]
    invoiced, billed = _invoiced(db, ids), _billed(db, ids)
    lines = _invoiced_assignments(db, [line.id for p in projects for pv in p.vendors for line in pv.services])
    by_service = _billed_by_service(db, ids)
    return [_view(p, invoiced, billed.get(p.id, 0.0), lines, by_service) for p in projects]


def _service_margins(project: Project, lines: dict[int, float], by_service: dict[tuple[int, int], float]):
    margins = []
    for s in project.services:
        assigned = [line for pv in project.vendors for line in pv.services if line.service_id == s.id]
        billed = by_service.get((project.id, s.id), 0.0)
        vendor_invoiced = round(sum(lines.get(line.id, 0.0) for line in assigned), 2)
        margins.append(ServiceMarginOut(
            service_id=s.id, service_title=s.title, billed=billed,
            vendor_committed=round(sum(line.agreed_cost for line in assigned), 2),
            vendor_invoiced=vendor_invoiced, margin=round(billed - vendor_invoiced, 2),
        ))
    return margins


def _view(
    project: Project,
    invoiced: dict[tuple[int, int], float],
    billed: float,
    lines: dict[int, float] | None = None,
    by_service: dict[tuple[int, int], float] | None = None,
) -> ProjectOut:
    lines, by_service = lines or {}, by_service or {}
    vendors = []
    for pv in project.vendors:
        spent = invoiced.get((project.id, pv.vendor_id), 0.0)
        services = [
            AssignedServiceOut(
                id=line.id, code=line.code, service_id=line.service_id, service_title=line.service.title,
                sac_code=line.service.sac_code, agreed_cost=line.agreed_cost, invoiced=lines.get(line.id, 0.0),
                over_invoiced=lines.get(line.id, 0.0) > line.agreed_cost,
            )
            for line in pv.services
        ]
        vendors.append(ProjectVendorOut(
            id=pv.id,
            vendor_id=pv.vendor_id,
            vendor_name=pv.vendor.business_name,
            vendor_email=pv.vendor.email,
            is_group_company=pv.vendor.is_group_company,
            services=services,
            agreed_cost=pv.agreed_cost,
            invoiced=spent,
            over_invoiced=spent > pv.agreed_cost,
            work_order_number=pv.work_order_number,
            work_order_date=pv.work_order_date,
            work_order_sent_at=pv.work_order_sent_at,
            work_order_outdated=pv.work_order_outdated,
        ))
    committed = round(sum(v.agreed_cost for v in vendors), 2)
    vendor_cost = round(sum(v.invoiced for v in vendors), 2)
    contract = project.contract_value or 0.0
    services = project.services
    service = next((s for s in services if s.id == project.service_id), services[0] if services else None)
    return ProjectOut(
        id=project.id,
        code=project.code,
        title=project.title,
        brand=project.brand,
        description=project.description,
        client_id=project.client_id,
        client_name=project.client.business_name if project.client else None,
        services=[ProjectServiceOut.model_validate(s) for s in services],
        service_id=service.id if service else None,
        service_title=service.title if service else None,
        sac_code=service.sac_code if service else None,
        gst_rate=service.gst_rate if service else None,
        budget=project.budget,
        start_date=project.start_date,
        end_date=project.end_date,
        expected_report_date=project.expected_report_date,
        status=project.status,
        vendors=vendors,
        committed_cost=committed,
        invoiced_cost=vendor_cost,
        remaining_budget=round(project.budget - committed, 2),
        over_budget=committed > project.budget,
        contract_value=contract,
        billed=billed,
        left_to_bill=round(contract - billed, 2) if contract > 0 else None,
        over_contract=contract > 0 and billed > contract,
        margin=round(billed - vendor_cost, 2),
        service_margins=_service_margins(project, lines, by_service),
        created_at=project.created_at,
    )
