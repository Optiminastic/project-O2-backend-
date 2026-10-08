"""Project rules: vendor shares, budget use, report dates and work order numbers."""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Project, ProjectVendor, Vendor, VendorInvoice
from app.schemas.project import ProjectOut, ProjectVendorIn, ProjectVendorOut
from app.services.numbering import business_date, month_end, next_number

WORK_ORDER_PREFIX = "WO"
FULL_WORK = 100.0


def default_report_date(start: date | None, end: date | None) -> date:
    """Reports are due on the last day of the month the work ends in."""
    return month_end(end or start or business_date())


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
    """The vendors named in ``shares``, after checking each appears once and the shares fit in 100%."""
    ids = [s.vendor_id for s in shares]
    if len(ids) != len(set(ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A vendor is listed twice. Give each vendor one row.")
    found = {v.id: v for v in db.query(Vendor).filter(Vendor.id.in_(ids)).all()} if ids else {}
    if len(found) != len(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor not found")
    total = round(sum(s.work_percent for s in shares), 2)
    if total > FULL_WORK:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The vendors' shares add up to {total:g}%. Together they can cover at most 100% of the work.",
        )
    return found


def replace_vendors(db: Session, project: Project, shares: list[ProjectVendorIn]) -> None:
    """Make the project's vendors match ``shares``, keeping each kept vendor's work order."""
    vendors = checked_vendors(db, shares)
    wanted = {s.vendor_id: s for s in shares}
    invoiced = _invoiced(db, [project.id]) if project.id else {}

    blocked = [pv.vendor.business_name for pv in project.vendors
               if pv.vendor_id not in wanted and (project.id, pv.vendor_id) in invoiced]
    if blocked:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{', '.join(blocked)} already invoiced this project, so they cannot be removed from it.",
        )

    kept = []
    for pv in project.vendors:
        share = wanted.pop(pv.vendor_id, None)
        if share is None:
            continue
        changed = (pv.work_percent, pv.agreed_cost) != (share.work_percent, share.agreed_cost)
        if changed and pv.work_order_number:
            pv.work_order_outdated = True
        pv.work_percent, pv.agreed_cost = share.work_percent, share.agreed_cost
        kept.append(pv)
    for share in shares:
        if share.vendor_id in wanted:
            kept.append(ProjectVendor(vendor=vendors[share.vendor_id], vendor_id=share.vendor_id,
                                      work_percent=share.work_percent, agreed_cost=share.agreed_cost))
    project.vendors = kept


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


def assert_vendor_on_project(db: Session, project_id: int, vendor_id: int) -> None:
    """A vendor invoice can bill a project only if that vendor works on it."""
    if not db.get(Project, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    on_project = db.query(ProjectVendor).filter_by(project_id=project_id, vendor_id=vendor_id).first()
    if not on_project:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This vendor is not on that project. Add them to the project first.")


def has_vendor_invoices(db: Session, project_id: int) -> bool:
    return db.query(VendorInvoice.id).filter(VendorInvoice.project_id == project_id).first() is not None


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


def project_views(db: Session, projects: list[Project]) -> list[ProjectOut]:
    invoiced = _invoiced(db, [p.id for p in projects])
    return [_view(p, invoiced) for p in projects]


def _view(project: Project, invoiced: dict[tuple[int, int], float]) -> ProjectOut:
    vendors = []
    for pv in project.vendors:
        spent = invoiced.get((project.id, pv.vendor_id), 0.0)
        vendors.append(ProjectVendorOut(
            id=pv.id,
            vendor_id=pv.vendor_id,
            vendor_name=pv.vendor.business_name,
            vendor_email=pv.vendor.email,
            work_percent=pv.work_percent,
            agreed_cost=pv.agreed_cost,
            invoiced=spent,
            over_invoiced=spent > pv.agreed_cost,
            work_order_number=pv.work_order_number,
            work_order_date=pv.work_order_date,
            work_order_sent_at=pv.work_order_sent_at,
            work_order_outdated=pv.work_order_outdated,
        ))
    committed = round(sum(v.agreed_cost for v in vendors), 2)
    return ProjectOut(
        id=project.id,
        code=project.code,
        title=project.title,
        brand=project.brand,
        description=project.description,
        client_id=project.client_id,
        client_name=project.client.business_name if project.client else None,
        budget=project.budget,
        start_date=project.start_date,
        end_date=project.end_date,
        expected_report_date=project.expected_report_date,
        status=project.status,
        vendors=vendors,
        work_percent_assigned=round(sum(v.work_percent for v in vendors), 2),
        committed_cost=committed,
        invoiced_cost=round(sum(v.invoiced for v in vendors), 2),
        remaining_budget=round(project.budget - committed, 2),
        over_budget=committed > project.budget,
        created_at=project.created_at,
    )
