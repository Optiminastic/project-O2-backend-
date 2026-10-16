from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import AuditLog, ClientInvoice, CompanyBank, Payment, PaymentMode, Project, User, UserRole, InvoiceStatus
from app.schemas.invoice import (
    BulkCreatedOut,
    BulkPreviewOut,
    BulkRowOut,
    InvoiceCreate,
    InvoiceUpdate,
    InvoiceOut,
    InvoiceDetail,
    InvoiceDocumentView,
    ManualApprovalIn,
    PaymentCreate,
    PaymentOut,
    ReturnIn,
    TimelineEntry,
    WorkflowResult,
)
from app.services import bulk_invoices as bulk
from app.services import invoice_document
from app.services import invoice_workflow as workflow
from app.services.invoice_lines import (
    LineInput,
    apply_financials,
    build_lines,
    interstate_for,
    lines_changed,
    primary_service_id,
)
from app.services.invoice_lock import SETTLEMENT_TOLERANCE, assert_editable, recompute_invoice, settled_amount
from app.services.audit import log_action
from app.services.contract import assert_within_contract
from app.services.numbering import PROFORMA_PREFIX, business_date, month_end, next_number
from app.services.agents import resolve_agent_id
from app.services.service_catalog import usable_project
from app.core.period import Period, month_period

router = APIRouter(prefix="/invoices", tags=["invoices"])


FINANCE_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER, UserRole.FINANCE_EXECUTIVE)


def _get_invoice(db: Session, invoice_id: int) -> ClientInvoice:
    inv = db.get(ClientInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


def _checked_proforma_date(value: date | None) -> date:
    """Today, or a date earlier this month: a proforma can be backdated, but not into a closed month."""
    today = business_date()
    if value is None:
        return today
    if value > today:
        raise HTTPException(400, "The invoice date cannot be in the future.")
    if value < today.replace(day=1):
        raise HTTPException(400, f"The invoice date can be backdated only to 1 {today:%b %Y}.")
    return value


def _new_proforma(
    db: Session,
    user: User,
    project: Project,
    lines: list[LineInput],
    *,
    proforma_date: date | None = None,
    due_date: date | None = None,
    agent_id: int | None = None,
    is_interstate: bool = False,
    **fields,
) -> ClientInvoice:
    """A draft proforma billing ``project``: its client, and one line per service in ``lines``."""
    assert_within_contract(db, project, sum(line.taxable_value for line in lines))
    dated = proforma_date or business_date()
    inv = ClientInvoice(
        **fields,
        project_id=project.id,
        client_id=project.client_id,
        # Credit the client's own agent unless another is chosen.
        agent_id=resolve_agent_id(db, agent_id or project.client.agent_id),
        is_interstate=interstate_for(project.client, is_interstate),
        proforma_number=next_number(db, PROFORMA_PREFIX, business_date()),
        proforma_date=dated,
        # Invoices are due at the end of the month unless another date is chosen.
        due_date=due_date or month_end(dated),
        status=InvoiceStatus.DRAFT,
        created_by_id=user.id,
        tds_applicable=True,  # clients always deduct TDS; only the rate varies
    )
    inv.lines = build_lines(project, lines)
    apply_financials(inv)
    db.add(inv)
    db.flush()
    return inv


def _line_inputs(project: Project, lines, taxable_value: float | None, description: str | None) -> list[LineInput]:
    """The lines asked for, or one line on the project's first service for a single amount."""
    if lines is not None:
        return [LineInput(l.service_id, l.taxable_value, l.description) for l in lines]
    return [LineInput(primary_service_id(project), taxable_value, description)]


def _workflow_result(db: Session, inv: ClientInvoice, delivery: workflow.LinkDelivery | None = None) -> WorkflowResult:
    db.commit()
    db.refresh(inv)
    return WorkflowResult(
        invoice=InvoiceDetail.model_validate(inv),
        link_url=delivery.url if delivery else None,
        emailed=delivery.emailed if delivery else False,
    )


@router.get("", response_model=list[InvoiceOut])
def list_invoices(
    search: str | None = Query(None),
    status: InvoiceStatus | None = Query(None),
    client_id: int | None = Query(None),
    agent_id: int | None = Query(None),
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(ClientInvoice)
    if period:
        q = q.filter(period.dates(func.coalesce(ClientInvoice.invoice_date, ClientInvoice.proforma_date)))
    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                ClientInvoice.invoice_number.ilike(like),
                ClientInvoice.proforma_number.ilike(like),
                ClientInvoice.service_description.ilike(like),
            )
        )
    if status:
        q = q.filter(ClientInvoice.status == status)
    if client_id:
        q = q.filter(ClientInvoice.client_id == client_id)
    if agent_id:
        q = q.filter(ClientInvoice.agent_id == agent_id)
    return q.order_by(ClientInvoice.created_at.desc()).all()


@router.post("", response_model=InvoiceDetail, status_code=201)
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE_ROLES)),
):
    project = usable_project(db, payload.project_id)
    inv = _new_proforma(
        db,
        user,
        project,
        _line_inputs(project, payload.lines, payload.taxable_value, payload.service_description),
        proforma_date=_checked_proforma_date(payload.proforma_date),
        due_date=payload.due_date,
        agent_id=payload.agent_id,
        is_interstate=payload.is_interstate,
        tds_rate=payload.tds_rate,
        supporting_document=payload.supporting_document,
        internal_remarks=payload.internal_remarks,
    )
    log_action(db, user, "Created proforma", "ClientInvoice", inv.id, inv.proforma_number)
    db.commit()
    db.refresh(inv)
    return inv


# ---------- Bulk upload ----------
# Declared before the /{invoice_id} routes so "bulk" is never read as an id.

TEMPLATE_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/bulk/template")
def bulk_template(db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE_ROLES))):
    return Response(
        content=bulk.build_template(db),
        media_type=TEMPLATE_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="bulk-invoice-template.xlsx"'},
    )


async def _checked_upload(db: Session, file: UploadFile) -> list[bulk.RowCheck]:
    content = await file.read(bulk.MAX_FILE_BYTES + 1)
    return bulk.check_rows(db, bulk.read_rows(file.filename or "", content))


def _row_out(c: bulk.RowCheck) -> BulkRowOut:
    gst = c.gst
    return BulkRowOut(
        row=c.row,
        project_input=c.project_input,
        project_code=c.project.code if c.project else None,
        client_name=c.client.business_name if c.client else None,
        service_title=c.service.title if c.service else None,
        amount=c.amount,
        gst_rate=c.service.gst_rate if c.service else None,
        gst_amount=gst.gst_amount if gst else None,
        total_amount=gst.total if gst else None,
        description=c.description,
        due_date=c.due_date,
        tds_rate=c.tds_rate,
        is_interstate=c.is_interstate,
        errors=c.errors,
        warnings=c.warnings,
    )


@router.post("/bulk/preview", response_model=BulkPreviewOut)
async def bulk_preview(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE_ROLES)),
):
    """Check an upload without creating anything."""
    checks = await _checked_upload(db, file)
    rows = [_row_out(c) for c in checks]
    error_count = sum(1 for r in rows if r.errors)
    return BulkPreviewOut(
        rows=rows,
        ready=error_count == 0,
        invoice_count=len(rows),
        error_count=error_count,
        client_count=len({c.client.id for c in checks if c.client}),
        taxable_total=round(sum(r.amount or 0 for r in rows), 2),
        gst_total=round(sum(r.gst_amount or 0 for r in rows), 2),
        grand_total=round(sum(r.total_amount or 0 for r in rows), 2),
    )


@router.post("/bulk", response_model=BulkCreatedOut, status_code=201)
async def bulk_create(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE_ROLES)),
):
    """Create one draft proforma per row, all in one transaction, or none if any row has an error."""
    checks = await _checked_upload(db, file)
    bad = sum(1 for c in checks if c.errors)
    if bad:
        noun = "row has" if bad == 1 else "rows have"
        raise HTTPException(422, f"Nothing was created: {bad} {noun} problems. Fix them and upload again.")

    created = [
        _new_proforma(
            db,
            user,
            c.project,
            [LineInput(c.service.id, c.amount, c.description)],
            due_date=c.due_date,
            is_interstate=c.is_interstate,
            tds_rate=c.tds_rate,
            internal_remarks=c.remarks,
        )
        for c in checks
    ]

    numbers = f"{created[0].proforma_number} to {created[-1].proforma_number}"
    log_action(db, user, "Bulk uploaded proformas", "ClientInvoice", None, f"{len(created)} proformas, {numbers}")
    db.commit()
    return BulkCreatedOut(created=len(created), invoices=created)


@router.get("/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _get_invoice(db, invoice_id)


@router.get("/{invoice_id}/document", response_model=InvoiceDocumentView)
def get_invoice_document(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The printable proforma / tax invoice, exactly as the client sees it."""
    return invoice_document.document(_get_invoice(db, invoice_id))


@router.patch("/{invoice_id}", response_model=InvoiceDetail)
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE_ROLES)),
):
    inv = _get_invoice(db, invoice_id)

    incoming = payload.model_dump(exclude_unset=True)
    new_lines = _edited_lines(db, inv, payload, incoming)
    if "agent_id" in incoming:
        incoming["agent_id"] = resolve_agent_id(db, incoming["agent_id"])
    if incoming.get("proforma_date") is not None and incoming["proforma_date"] != inv.proforma_date:
        _checked_proforma_date(incoming["proforma_date"])
    if incoming.get("status") is not None:
        workflow.assert_manual_status(inv, incoming["status"])
    workflow.before_edit(db, inv, user)
    # Enforce locking on critical financial fields (unless CEO override).
    violations = assert_editable(inv, incoming)
    if new_lines is not None and inv.is_locked and lines_changed(inv, new_lines[1]):
        violations.append("lines")
    if violations and user.role != UserRole.ADMIN_CEO:
        raise HTTPException(
            423,
            f"Invoice is locked after payment. Cannot edit: {', '.join(sorted(violations))}. "
            "Raise a credit/debit note or request a CEO correction.",
        )

    for k, v in incoming.items():
        setattr(inv, k, v)
    if new_lines is not None:
        project, inputs = new_lines
        inv.lines = build_lines(project, inputs)
    if not inv.is_issued:
        # A proforma follows the client's GSTIN state; an issued invoice keeps the tax it went out with.
        inv.is_interstate = interstate_for(inv.client, inv.is_interstate)
    apply_financials(inv)
    recompute_invoice(inv)
    if new_lines is not None and not inv.is_issued:
        assert_within_contract(db, new_lines[0], inv.taxable_value, exclude_invoice_id=inv.id)
    note = "CEO correction on locked invoice" if violations else "Updated invoice"
    log_action(db, user, note, "ClientInvoice", inv.id, ", ".join(violations) or None)
    db.commit()
    db.refresh(inv)
    return inv


def _edited_lines(
    db: Session, inv: ClientInvoice, payload: InvoiceUpdate, incoming: dict
) -> tuple[Project, list[LineInput]] | None:
    """The project and lines an edit asks for, or None to keep the lines.

    ``incoming`` loses the line fields, and gains the client of a newly chosen project.
    """
    for field in ("lines", "taxable_value", "service_description"):
        incoming.pop(field, None)
    project_id = payload.project_id if payload.project_id is not None else inv.project_id
    moved = project_id != inv.project_id
    sent = payload.model_fields_set
    if moved:
        project = usable_project(db, project_id)
        incoming["client_id"] = project.client_id
    elif not sent & {"lines", "taxable_value", "service_description"}:
        return None
    elif inv.project is None:
        _legacy_edit(payload, incoming)
        return None
    else:
        project = inv.project

    if payload.lines is not None:
        return project, _line_inputs(project, payload.lines, None, None)
    if len(inv.lines) > 1 and not moved:
        raise HTTPException(400, "This invoice has several lines. Send every line to change an amount.")
    current = inv.line_items[0]
    amount = payload.taxable_value if payload.taxable_value is not None else inv.taxable_value
    if "service_description" in sent:
        description = payload.service_description
    else:
        # Another project's service brings its own description.
        description = None if moved else current.description
    service_id = current.service_id if not moved and current.service_id else primary_service_id(project)
    return project, [LineInput(service_id, amount, description)]


def _legacy_edit(payload: InvoiceUpdate, incoming: dict) -> None:
    """An invoice raised before projects keeps its single header line."""
    if payload.lines is not None:
        raise HTTPException(400, "This invoice was raised before projects. Choose a project to bill by service.")
    if payload.taxable_value is not None:
        incoming["taxable_value"] = payload.taxable_value
    if "service_description" in payload.model_fields_set:
        incoming["service_description"] = payload.service_description


@router.delete("/{invoice_id}", status_code=204)
def delete_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)),
):
    inv = _get_invoice(db, invoice_id)
    if inv.status not in workflow.DELETABLE:
        raise HTTPException(400, "Only proformas that are not with the client can be deleted")
    db.delete(inv)
    log_action(db, user, "Deleted proforma", "ClientInvoice", invoice_id, inv.display_number)
    db.commit()


@router.post("/{invoice_id}/duplicate", response_model=InvoiceDetail, status_code=201)
def duplicate_invoice(
    invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE_ROLES))
):
    src = _get_invoice(db, invoice_id)
    if src.project_id is None:
        raise HTTPException(400, "This invoice was raised before projects. Raise a new one from its project.")
    project = usable_project(db, src.project_id)
    known = {svc.id for svc in project.services}
    lines = [
        LineInput(l.service_id if l.service_id in known else primary_service_id(project), l.taxable_value, l.description)
        for l in src.line_items
    ]
    inv = _new_proforma(
        db,
        user,
        project,
        lines,
        agent_id=src.agent_id,
        is_interstate=src.is_interstate,
        tds_rate=src.tds_rate,
    )
    log_action(
        db, user, "Created proforma", "ClientInvoice", inv.id, f"{inv.proforma_number}, copy of {src.display_number}"
    )
    db.commit()
    db.refresh(inv)
    return inv


@router.post("/{invoice_id}/payments", response_model=InvoiceDetail, status_code=201)
def record_payment(
    invoice_id: int,
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE_ROLES)),
):
    inv = _get_invoice(db, invoice_id)
    if not inv.is_issued or inv.status == InvoiceStatus.CANCELLED:
        raise HTTPException(400, "Receipts can only be recorded against an issued, active tax invoice")
    if inv.invoice_date and payload.payment_date < inv.invoice_date:
        raise HTTPException(
            400, f"The receipt date cannot be before the invoice date ({inv.invoice_date:%d %b %Y})."
        )
    remaining = round(inv.total_amount - settled_amount(inv), 2)
    tds_so_far = round(sum(p.tds_deducted or 0.0 for p in inv.payments), 2)
    tds = payload.tds_deducted
    if tds is None:
        tds = _tds_with_receipt(remaining, payload.amount, round(max(inv.expected_tds - tds_so_far, 0.0), 2))
    settling = round(payload.amount + tds, 2)
    if settling > remaining + SETTLEMENT_TOLERANCE:
        raise HTTPException(
            400,
            f"Receipt of {workflow.format_inr(payload.amount)} plus TDS of {workflow.format_inr(tds)} "
            f"is more than the {workflow.format_inr(remaining)} still due on this invoice.",
        )
    if tds_so_far + tds > inv.expected_tds + SETTLEMENT_TOLERANCE:
        raise HTTPException(
            400,
            f"TDS of {workflow.format_inr(tds_so_far + tds)} is more than the "
            f"{workflow.format_inr(inv.expected_tds)} expected at {inv.tds_rate:g}%. Check the amount the client deducted.",
        )

    payment = Payment(
        payment_mode=PaymentMode.BANK,
        **{**payload.model_dump(), "tds_deducted": tds, "bank_name": _receiving_bank_name(db, payload)},
    )
    # Through the relationship, so the payments already loaded for the checks above include it.
    inv.payments.append(payment)
    db.flush()
    # This locks the invoice and recomputes status.
    recompute_invoice(inv)
    log_action(
        db, user, "Recorded receipt", "ClientInvoice", inv.id,
        f"₹{payload.amount:.2f} · invoice locked",
    )
    db.commit()
    db.refresh(inv)
    return inv


def _receiving_bank_name(db: Session, payload: PaymentCreate) -> str:
    """The bank as the receipt records it: one of ours by id, or the name typed."""
    if payload.bank_account_id is None:
        return payload.bank_name
    bank = db.get(CompanyBank, payload.bank_account_id)
    if bank is None:
        raise HTTPException(404, "Bank not found")
    if not bank.is_active:
        raise HTTPException(400, f"{bank.label} is deactivated. Choose another bank.")
    return bank.label


def _tds_with_receipt(remaining: float, amount: float, tds_left: float) -> float:
    """TDS counted with a receipt when the form does not ask for it.

    Clients pay net of TDS, so once the cash clears everything but the TDS still
    expected, that TDS is taken as deducted. Earlier part payments count none.
    """
    if amount < remaining - tds_left - SETTLEMENT_TOLERANCE:
        return 0.0
    return round(min(tds_left, max(remaining - amount, 0.0)), 2)


@router.get("/{invoice_id}/payments", response_model=list[PaymentOut])
def list_payments(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _get_invoice(db, invoice_id).payments


# ---------- Approval workflow ----------


@router.post("/{invoice_id}/send-to-client", response_model=WorkflowResult)
def send_to_client(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    inv = _get_invoice(db, invoice_id)
    return _workflow_result(db, inv, workflow.send_to_client(db, inv, user))


@router.post("/{invoice_id}/client-approval", response_model=InvoiceDetail)
def record_client_approval(
    invoice_id: int,
    payload: ManualApprovalIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    inv = _get_invoice(db, invoice_id)
    workflow.approve_manually(db, inv, user, payload.note)
    db.commit()
    db.refresh(inv)
    return inv


@router.post("/{invoice_id}/issue", response_model=WorkflowResult)
def issue_tax_invoice(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    inv = _get_invoice(db, invoice_id)
    return _workflow_result(db, inv, workflow.issue(db, inv, user))


@router.post("/{invoice_id}/return", response_model=InvoiceDetail)
def return_for_changes(
    invoice_id: int, payload: ReturnIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    inv = _get_invoice(db, invoice_id)
    workflow.return_for_changes(db, inv, user, payload.reason)
    db.commit()
    db.refresh(inv)
    return inv


@router.get("/{invoice_id}/timeline", response_model=list[TimelineEntry])
def invoice_timeline(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _get_invoice(db, invoice_id)
    return (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == workflow.ENTITY, AuditLog.entity_id == invoice_id)
        .order_by(AuditLog.id)
        .all()
    )
