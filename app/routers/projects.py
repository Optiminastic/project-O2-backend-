from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.core.deps import get_current_user, require_roles
from app.core.period import Period, month_period
from app.core.rbac import NON_EXEC
from app.database import get_db
from app.models import Client, ClientInvoice, Project, ProjectVendor, User, Vendor, VendorInvoice
from app.models.mixins import utcnow
from app.schemas.project import (
    ProjectClientInvoiceOut,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectUpdate,
    ProjectVendorInvoiceOut,
    WorkOrderSent,
)
from app.services import projects as rules
from app.services.audit import log_action
from app.services.group_companies import RESTRICTED_NAME, assert_may_manage, masked_name, sees_group_companies
from app.services.service_catalog import usable_services
from app.services.email import send_work_order_email
from app.services.invoice_workflow import format_inr
from app.services.work_order import render_work_order_pdf, work_order_filename

router = APIRouter(prefix="/projects", tags=["projects"])

# Creating or changing a project commits spending, so executives can only view.
EDIT_ROLES = NON_EXEC


def _load(db: Session, project_id: int) -> Project:
    project = (
        db.query(Project)
        .options(
            selectinload(Project.vendors).selectinload(ProjectVendor.vendor),
            selectinload(Project.client),
            selectinload(Project.services),
        )
        .filter(Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _masked(view: ProjectOut, user: User) -> ProjectOut:
    """A group company on the project shows its cost but not who it is, except to the CFO and CEO."""
    if not sees_group_companies(user):
        for v in view.vendors:
            if v.is_group_company:
                v.vendor_name, v.vendor_email = RESTRICTED_NAME, ""
    return view


def _guard_group_vendors(db: Session, project: Project | None, incoming: list, user: User) -> None:
    """Only the CFO and CEO add, change or remove a group company on a project."""
    if sees_group_companies(user):
        return
    before = {pv.vendor_id: (pv.work_percent, pv.agreed_cost) for pv in (project.vendors if project else [])
              if pv.vendor.is_group_company}
    ids = [v.vendor_id for v in incoming]
    group_ids = {v.id for v in db.query(Vendor).filter(Vendor.id.in_(ids), Vendor.is_group_company.is_(True))} if ids else set()
    after = {v.vendor_id: (v.work_percent, v.agreed_cost) for v in incoming if v.vendor_id in group_ids}
    if after != before:
        assert_may_manage(user, True)


def _detail(db: Session, project: Project, user: User) -> ProjectDetail:
    (view,) = rules.project_views(db, [project])
    view = _masked(view, user)
    names = {pv.vendor_id: masked_name(pv.vendor, user) for pv in project.vendors}
    invoices = (
        db.query(VendorInvoice)
        .filter(VendorInvoice.project_id == project.id)
        .order_by(VendorInvoice.invoice_date.desc(), VendorInvoice.id.desc())
        .all()
    )
    client_invoices = (
        db.query(ClientInvoice)
        .filter(ClientInvoice.project_id == project.id)
        .order_by(ClientInvoice.created_at.desc())
        .all()
    )
    return ProjectDetail(
        **view.model_dump(),
        client_invoices=[ProjectClientInvoiceOut.model_validate(i) for i in client_invoices],
        vendor_invoices=[
            ProjectVendorInvoiceOut(
                id=i.id,
                vendor_id=i.vendor_id,
                vendor_name=names.get(i.vendor_id, ""),
                invoice_number=i.invoice_number,
                invoice_date=i.invoice_date,
                invoice_amount=i.invoice_amount,
                net_payable=i.net_payable,
                status=i.status.value,
            )
            for i in invoices
        ],
    )


def _require_client(db: Session, client_id: int) -> None:
    if not db.get(Client, client_id):
        raise HTTPException(404, "Client not found")


@router.get("", response_model=list[ProjectOut])
def list_projects(
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Project).options(
        selectinload(Project.vendors).selectinload(ProjectVendor.vendor),
        selectinload(Project.client),
        selectinload(Project.services),
    )
    if period:
        # Running at any point in the month: started before it ends and not finished before it starts.
        q = q.filter(
            or_(Project.start_date.is_(None), Project.start_date < period.end),
            or_(Project.end_date.is_(None), Project.end_date >= period.start),
        )
    return [_masked(v, user) for v in rules.project_views(db, q.order_by(Project.created_at.desc()).all())]


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*EDIT_ROLES)),
):
    _require_client(db, payload.client_id)
    services = usable_services(db, payload.service_ids)
    start = rules.month_start(payload.start_date)
    rules.assert_dates(start, payload.end_date)
    if payload.code:
        rules.assert_code_free(db, payload.code)

    project = Project(
        **payload.model_dump(exclude={"vendors", "service_ids", "service_id", "code", "start_date"}),
        code=payload.code or rules.next_project_code(db),
        start_date=start,
        created_by_id=user.id,
    )
    rules.set_services(project, services)
    project.expected_report_date = payload.expected_report_date or rules.default_report_date(start)
    _guard_group_vendors(db, None, payload.vendors, user)
    rules.replace_vendors(db, project, payload.vendors)
    db.add(project)
    db.flush()
    log_action(db, user, "Created project", "Project", project.id, f"{project.code} {project.title}")
    db.commit()
    return _detail(db, _load(db, project.id), user)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _detail(db, _load(db, project_id), user)


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*EDIT_ROLES)),
):
    project = _load(db, project_id)
    changes = payload.model_dump(exclude_unset=True, exclude={"vendors", "service_ids", "service_id"})
    # A project always has a code, title and client; an explicit null leaves them as they are.
    for required in ("code", "title", "client_id", "budget", "contract_value", "status"):
        if changes.get(required, ...) is None:
            changes.pop(required)
    if "start_date" in changes:
        changes["start_date"] = rules.month_start(changes["start_date"])
    if "client_id" in changes:
        _require_client(db, changes["client_id"])
    if payload.service_ids is not None:
        kept = {s.id for s in project.services}
        # A service already on the project stays usable even if since deactivated.
        new_ids = [sid for sid in payload.service_ids if sid not in kept]
        added = usable_services(db, new_ids) if new_ids else []
        by_id = {s.id: s for s in [*project.services, *added]}
        rules.set_services(project, [by_id[sid] for sid in dict.fromkeys(payload.service_ids)])
    if "code" in changes:
        rules.assert_code_free(db, changes["code"], exclude_id=project.id)
    rules.assert_dates(changes.get("start_date", project.start_date), changes.get("end_date", project.end_date))

    for field, value in changes.items():
        setattr(project, field, value)
    moved_start = "start_date" in changes and "expected_report_date" not in changes
    if moved_start or not project.expected_report_date:
        project.expected_report_date = rules.default_report_date(project.start_date)
    if payload.vendors is not None:
        _guard_group_vendors(db, project, payload.vendors, user)
        rules.replace_vendors(db, project, payload.vendors)
    log_action(db, user, "Updated project", "Project", project.id, project.code)
    db.commit()
    return _detail(db, _load(db, project.id), user)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*EDIT_ROLES)),
):
    project = _load(db, project_id)
    if rules.has_vendor_invoices(db, project.id):
        raise HTTPException(409, "Vendors have already invoiced this project, so it cannot be deleted.")
    if rules.has_client_invoices(db, project.id):
        raise HTTPException(409, "This project has client invoices, so it cannot be deleted.")
    log_action(db, user, "Deleted project", "Project", project.id, f"{project.code} {project.title}")
    db.delete(project)
    db.commit()
    return Response(status_code=204)


# ---------- Work orders ----------

def _work_order(db: Session, project_id: int, vendor_id: int, user: User) -> tuple[Project, ProjectVendor, bytes]:
    project = _load(db, project_id)
    pv = rules.project_vendor(project, vendor_id)
    assert_may_manage(user, pv.vendor.is_group_company)
    rules.ensure_work_order_number(db, pv)
    return project, pv, render_work_order_pdf(project, pv)


@router.get("/{project_id}/vendors/{vendor_id}/work-order.pdf")
def download_work_order(
    project_id: int,
    vendor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*EDIT_ROLES)),
):
    """The vendor's work order. The first download gives it its permanent WO number."""
    project, pv, pdf = _work_order(db, project_id, vendor_id, user)
    log_action(db, user, "Downloaded work order", "Project", project.id, pv.work_order_number)
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{work_order_filename(pv)}"'},
    )


@router.post("/{project_id}/vendors/{vendor_id}/work-order/send", response_model=WorkOrderSent)
def send_work_order(
    project_id: int,
    vendor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*EDIT_ROLES)),
):
    project, pv, pdf = _work_order(db, project_id, vendor_id, user)
    emailed = send_work_order_email(
        to_email=pv.vendor.email,
        vendor_name=pv.vendor.business_name,
        work_order_number=pv.work_order_number,
        project_label=f"{project.code} {project.title}",
        agreed_cost=format_inr(pv.agreed_cost),
        report_due=project.expected_report_date.strftime("%d %b %Y") if project.expected_report_date else "-",
        pdf=pdf,
        filename=work_order_filename(pv),
    )
    if emailed:
        pv.work_order_sent_at = utcnow()
        pv.work_order_outdated = False
    action = "Emailed work order" if emailed else "Work order email not sent (email not set up)"
    log_action(db, user, action, "Project", project.id, f"{pv.work_order_number} to {pv.vendor.email}")
    db.commit()
    return WorkOrderSent(work_order_number=pv.work_order_number, to_email=pv.vendor.email, emailed=emailed)
