"""Cash: cash received and cash paid out to agents, each against UPS codes, with a month-wise overview."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, cast, func
from sqlalchemy.orm import Session, joinedload

from app.core.deps import get_current_user, require_roles
from app.core.period import Period, month_period
from app.core.rbac import NON_EXEC
from app.database import get_db
from app.models import Agent, ClientInvoice, InvoiceStatus, Project, ProjectVendor, ProjectVendorService, User, UserRole
from app.models.cash import CASH_IN, CASH_OUT, CashEntry, CashEntryLine
from app.schemas.cash import CashAgentOut, CashEntryCreate, CashEntryOut, CashLineOut, CashMonthOut, UpsCodeOut
from app.services.audit import log_action
from app.services.numbering import BUSINESS_TZ

router = APIRouter(prefix="/cash", tags=["cash"])

RECORD_ROLES = (*NON_EXEC, UserRole.FINANCE_EXECUTIVE)


def split_equally(amount: float, count: int) -> list[float]:
    """``amount`` in ``count`` equal shares, in paise; the first shares take any leftover paise."""
    paise = round(amount * 100)
    base, extra = divmod(paise, count)
    return [(base + (1 if i < extra else 0)) / 100 for i in range(count)]


def _out(entry: CashEntry) -> CashEntryOut:
    return CashEntryOut(
        id=entry.id,
        direction=entry.direction,
        entry_date=entry.entry_date,
        amount=entry.amount,
        agent_id=entry.agent_id,
        agent_name=entry.agent.business_name if entry.agent else None,
        note=entry.note,
        lines=[CashLineOut(assignment_id=l.assignment_id, code=l.assignment.code, amount=l.amount) for l in entry.lines],
        created_at=entry.created_at,
    )


def _with_details(db: Session):
    return db.query(CashEntry).options(
        joinedload(CashEntry.agent), joinedload(CashEntry.lines).joinedload(CashEntryLine.assignment)
    )


@router.get("/codes", response_model=list[UpsCodeOut])
def ups_codes(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Every UPS code, newest project first, to choose from."""
    rows = (
        db.query(ProjectVendorService)
        .join(ProjectVendor, ProjectVendorService.project_vendor_id == ProjectVendor.id)
        .join(Project, ProjectVendor.project_id == Project.id)
        .options(
            joinedload(ProjectVendorService.service),
            joinedload(ProjectVendorService.project_vendor).joinedload(ProjectVendor.project),
            joinedload(ProjectVendorService.project_vendor).joinedload(ProjectVendor.vendor),
        )
        .order_by(Project.id.desc(), ProjectVendorService.code)
        .all()
    )
    return [
        UpsCodeOut(
            id=r.id,
            code=r.code,
            project_id=r.project_vendor.project_id,
            project_code=r.project_vendor.project.code,
            project_title=r.project_vendor.project.title,
            service_title=r.service.title if r.service else "",
            vendor_name=r.project_vendor.vendor.business_name if r.project_vendor.vendor else "",
        )
        for r in rows
    ]


@router.get("/agents", response_model=list[CashAgentOut])
def cash_agents(db: Session = Depends(get_db), _: User = Depends(require_roles(*RECORD_ROLES))):
    """Active agents cash can be paid out to (finance executives cannot open the full agents list)."""
    rows = db.query(Agent).filter(Agent.is_active.is_(True)).order_by(Agent.is_house, Agent.business_name).all()
    return [CashAgentOut(id=a.id, business_name=a.business_name) for a in rows]


@router.get("", response_model=list[CashEntryOut])
def list_entries(
    direction: str | None = Query(None, pattern="^(IN|OUT)$"),
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = _with_details(db)
    if direction:
        q = q.filter(CashEntry.direction == direction)
    if period:
        q = q.filter(period.dates(CashEntry.entry_date))
    return [_out(e) for e in q.order_by(CashEntry.entry_date.desc(), CashEntry.id.desc()).all()]


@router.post("", response_model=CashEntryOut, status_code=201)
def record_entry(payload: CashEntryCreate, db: Session = Depends(get_db), user: User = Depends(require_roles(*RECORD_ROLES))):
    today = datetime.now(BUSINESS_TZ).date()
    if payload.entry_date > today:
        raise HTTPException(400, "The date cannot be in the future.")
    codes = db.query(ProjectVendorService).filter(ProjectVendorService.id.in_(payload.assignment_ids)).all()
    if len(codes) != len(payload.assignment_ids):
        raise HTTPException(404, "UPS code not found")

    agent = None
    if payload.direction == CASH_OUT:
        if payload.agent_id is None:
            raise HTTPException(400, "Choose the agent the cash is paid to.")
        agent = db.get(Agent, payload.agent_id)
        if agent is None:
            raise HTTPException(404, "Agent not found")
        if not agent.is_active:
            raise HTTPException(400, f"{agent.business_name} is inactive, so cash cannot be paid to them.")
    elif payload.agent_id is not None:
        raise HTTPException(400, "Cash received is not paid to an agent.")

    by_id = {c.id: c for c in codes}
    entry = CashEntry(
        direction=payload.direction,
        entry_date=payload.entry_date,
        amount=round(payload.amount, 2),
        agent_id=agent.id if agent else None,
        note=(payload.note or "").strip() or None,
        created_by_id=user.id,
        lines=[
            CashEntryLine(assignment_id=aid, assignment=by_id[aid], amount=share)
            for aid, share in zip(payload.assignment_ids, split_equally(payload.amount, len(payload.assignment_ids)))
        ],
    )
    db.add(entry)
    db.flush()
    what = "Recorded cash received" if payload.direction == CASH_IN else f"Recorded cash paid to {agent.business_name}"
    log_action(db, user, what, "CashEntry", entry.id, f"₹{entry.amount:,.2f} on {', '.join(by_id[a].code for a in payload.assignment_ids)}")
    db.commit()
    return _out(_with_details(db).filter(CashEntry.id == entry.id).one())


@router.delete("/{entry_id}", status_code=204)
def delete_entry(entry_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*NON_EXEC))):
    entry = db.get(CashEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Cash entry not found")
    log_action(db, user, "Deleted cash entry", "CashEntry", entry.id, f"{entry.direction} ₹{entry.amount:,.2f} on {entry.entry_date}")
    db.delete(entry)
    db.commit()


def _months_back(today: date, count: int) -> list[str]:
    """``count`` months ending with this one, oldest first, as YYYY-MM."""
    out, y, m = [], today.year, today.month
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


@router.get("/overview", response_model=list[CashMonthOut])
def overview(
    months: int = Query(12, ge=1, le=36),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Month by month: total invoice value raised (taxable + GST), and cash received and paid out."""
    keys = _months_back(datetime.now(BUSINESS_TZ).date(), months)
    start = date(int(keys[0][:4]), int(keys[0][5:]), 1)

    inv_day = func.coalesce(ClientInvoice.invoice_date, cast(ClientInvoice.created_at, Date))
    inv_month = func.to_char(inv_day, "YYYY-MM")
    invoices = {
        m: (taxable or 0.0, gst or 0.0, total or 0.0, n)
        for m, taxable, gst, total, n in db.query(
            inv_month, func.sum(ClientInvoice.taxable_value), func.sum(ClientInvoice.gst_amount),
            func.sum(ClientInvoice.total_amount), func.count(ClientInvoice.id),
        )
        .filter(ClientInvoice.status != InvoiceStatus.CANCELLED, inv_day >= start)
        .group_by(inv_month)
        .all()
    }
    cash_month = func.to_char(CashEntry.entry_date, "YYYY-MM")
    cash: dict[tuple[str, str], float] = {
        (m, d): total or 0.0
        for m, d, total in db.query(cash_month, CashEntry.direction, func.sum(CashEntry.amount))
        .filter(CashEntry.entry_date >= start)
        .group_by(cash_month, CashEntry.direction)
        .all()
    }
    rows = []
    for m in keys:
        taxable, gst, total, n = invoices.get(m, (0.0, 0.0, 0.0, 0))
        rows.append(CashMonthOut(
            month=m, invoice_taxable=round(taxable, 2), invoice_gst=round(gst, 2), invoice_total=round(total, 2),
            invoice_count=n, cash_received=round(cash.get((m, CASH_IN), 0.0), 2),
            cash_paid=round(cash.get((m, CASH_OUT), 0.0), 2),
        ))
    return rows
