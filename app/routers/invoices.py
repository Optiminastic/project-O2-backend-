from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import AuditLog, Client, ClientInvoice, Payment, PaymentMode, User, UserRole, InvoiceStatus
from app.schemas.invoice import (
    BulkCreatedOut,
    BulkPreviewOut,
    BulkRowOut,
    InvoiceCreate,
    InvoiceUpdate,
    InvoiceOut,
    InvoiceDetail,
    ManualApprovalIn,
    PaymentCreate,
    PaymentOut,
    ReturnIn,
    TimelineEntry,
    WorkflowResult,
)
from app.services import bulk_invoices as bulk
from app.services import invoice_workflow as workflow
from app.services.taxation import compute_gst, compute_tds
from app.services.invoice_lock import SETTLEMENT_TOLERANCE, assert_editable, recompute_invoice, settled_amount
from app.services.audit import log_action
from app.services.numbering import PROFORMA_PREFIX, business_date, month_end, next_number
from app.services.agents import resolve_agent_id
from app.services.service_catalog import service_snapshot, usable_service
from app.core.period import Period, month_period

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _apply_financials(inv: ClientInvoice) -> None:
    gst = compute_gst(inv.taxable_value, inv.gst_rate, inv.is_interstate)
    inv.gst_amount = gst.gst_amount
    inv.cgst, inv.sgst, inv.igst = gst.cgst, gst.sgst, gst.igst
    inv.total_amount = gst.total
    inv.expected_tds = compute_tds(inv.taxable_value, inv.tds_rate, inv.tds_applicable)
    # amount_received is None until the row is flushed (column default not yet applied),
    # so coalesce before comparing.
    if (inv.amount_received or 0) <= 0:
        inv.amount_pending = inv.total_amount


FINANCE_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER, UserRole.FINANCE_EXECUTIVE)


def _get_invoice(db: Session, invoice_id: int) -> ClientInvoice:
    inv = db.get(ClientInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


def _new_proforma(db: Session, user: User, **fields) -> ClientInvoice:
    today = business_date()
    # Invoices are due at the end of the month unless another date is chosen.
    fields["due_date"] = fields.get("due_date") or month_end(today)
    inv = ClientInvoice(
        **fields,
        proforma_number=next_number(db, PROFORMA_PREFIX, today),
        proforma_date=today,
        status=InvoiceStatus.DRAFT,
        created_by_id=user.id,
        tds_applicable=True,  # clients always deduct TDS; only the rate varies
    )
    _apply_financials(inv)
    db.add(inv)
    db.flush()
    return inv


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
    client = db.get(Client, payload.client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    data = payload.model_dump()
    # Credit the client's own agent unless another is chosen.
    data["agent_id"] = resolve_agent_id(db, data.get("agent_id") or client.agent_id)
    data.update(service_snapshot(usable_service(db, payload.service_id), data))
    inv = _new_proforma(db, user, **data)
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
        client_input=c.client_input,
        client_name=c.client.business_name if c.client else None,
        service_input=c.service_input,
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

    created = []
    for c in checks:
        data = {
            "client_id": c.client.id,
            "agent_id": resolve_agent_id(db, c.client.agent_id),
            "due_date": c.due_date,
            "taxable_value": c.amount,
            "is_interstate": c.is_interstate,
            "tds_rate": c.tds_rate,
            "internal_remarks": c.remarks,
        }
        data.update(service_snapshot(c.service, {"service_description": c.description}))
        created.append(_new_proforma(db, user, **data))

    numbers = f"{created[0].proforma_number} to {created[-1].proforma_number}"
    log_action(db, user, "Bulk uploaded proformas", "ClientInvoice", None, f"{len(created)} proformas, {numbers}")
    db.commit()
    return BulkCreatedOut(created=len(created), invoices=created)


@router.get("/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _get_invoice(db, invoice_id)


@router.patch("/{invoice_id}", response_model=InvoiceDetail)
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE_ROLES)),
):
    inv = _get_invoice(db, invoice_id)

    incoming = payload.model_dump(exclude_unset=True)
    if "agent_id" in incoming:
        incoming["agent_id"] = resolve_agent_id(db, incoming["agent_id"])
    if incoming.get("service_id") is not None and incoming["service_id"] != inv.service_id:
        # A new service re-applies its details unless the same request overrides them.
        incoming.update(service_snapshot(usable_service(db, incoming["service_id"]), incoming))
    if incoming.get("status") is not None:
        workflow.assert_manual_status(inv, incoming["status"])
    workflow.before_edit(db, inv, user)
    # Enforce locking on critical financial fields (unless CEO override).
    violations = assert_editable(inv, incoming)
    if violations and user.role != UserRole.ADMIN_CEO:
        raise HTTPException(
            423,
            f"Invoice is locked after payment. Cannot edit: {', '.join(sorted(violations))}. "
            "Raise a credit/debit note or request a CEO correction.",
        )

    for k, v in incoming.items():
        setattr(inv, k, v)
    _apply_financials(inv)
    recompute_invoice(inv)
    note = "CEO correction on locked invoice" if violations else "Updated invoice"
    log_action(db, user, note, "ClientInvoice", inv.id, ", ".join(violations) or None)
    db.commit()
    db.refresh(inv)
    return inv


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
    inv = _new_proforma(
        db,
        user,
        client_id=src.client_id,
        agent_id=src.agent_id,
        service_id=src.service_id,
        service_title=src.service_title,
        service_description=src.service_description,
        sac_code=src.sac_code,
        taxable_value=src.taxable_value,
        gst_rate=src.gst_rate,
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
        raise HTTPException(400, "Payments can only be recorded against an issued, active tax invoice")
    if payload.amount <= 0:
        raise HTTPException(400, "Payment amount must be positive")
    remaining = round(inv.total_amount - settled_amount(inv), 2)
    settling = round(payload.amount + payload.tds_deducted, 2)
    if settling > remaining + SETTLEMENT_TOLERANCE:
        raise HTTPException(
            400,
            f"Payment of {workflow.format_inr(payload.amount)} plus TDS of {workflow.format_inr(payload.tds_deducted)} "
            f"is more than the {workflow.format_inr(remaining)} still due on this invoice.",
        )
    tds_so_far = round(sum(p.tds_deducted or 0.0 for p in inv.payments), 2)
    if tds_so_far + payload.tds_deducted > inv.expected_tds + SETTLEMENT_TOLERANCE:
        raise HTTPException(
            400,
            f"TDS of {workflow.format_inr(tds_so_far + payload.tds_deducted)} is more than the "
            f"{workflow.format_inr(inv.expected_tds)} expected at {inv.tds_rate:g}%. Check the amount the client deducted.",
        )

    payment = Payment(payment_mode=PaymentMode.BANK, **payload.model_dump())
    # Through the relationship, so the payments already loaded for the checks above include it.
    inv.payments.append(payment)
    db.flush()
    # This locks the invoice and recomputes status.
    recompute_invoice(inv)
    log_action(
        db, user, "Recorded payment", "ClientInvoice", inv.id,
        f"₹{payload.amount:.2f} · invoice locked",
    )
    db.commit()
    db.refresh(inv)
    return inv


@router.get("/{invoice_id}/payments", response_model=list[PaymentOut])
def list_payments(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _get_invoice(db, invoice_id).payments


# ---------- Approval workflow ----------


@router.post("/{invoice_id}/send-to-client", response_model=WorkflowResult)
def send_to_client(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE_ROLES))):
    inv = _get_invoice(db, invoice_id)
    return _workflow_result(db, inv, workflow.send_to_client(db, inv, user))


@router.post("/{invoice_id}/client-approval", response_model=InvoiceDetail)
def record_client_approval(
    invoice_id: int,
    payload: ManualApprovalIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE_ROLES)),
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
