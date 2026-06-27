from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import Client, ClientInvoice, Payment, User, UserRole, InvoiceStatus
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceUpdate,
    InvoiceOut,
    InvoiceDetail,
    PaymentCreate,
    PaymentOut,
)
from app.services.taxation import compute_gst, compute_tds
from app.services.invoice_lock import recompute_invoice, assert_editable
from app.services.audit import log_action

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _apply_financials(inv: ClientInvoice) -> None:
    gst = compute_gst(inv.taxable_value, inv.gst_rate, inv.is_interstate)
    inv.gst_amount = gst.gst_amount
    inv.cgst, inv.sgst, inv.igst = gst.cgst, gst.sgst, gst.igst
    inv.total_amount = gst.total
    inv.expected_tds = compute_tds(inv.taxable_value, inv.tds_rate, inv.tds_applicable)
    if inv.amount_received <= 0:
        inv.amount_pending = inv.total_amount


def _next_invoice_number(db: Session) -> str:
    year = datetime.now(timezone.utc).year
    count = db.query(ClientInvoice).count() + 1
    return f"INV-{year}-{count:04d}"


@router.get("", response_model=list[InvoiceOut])
def list_invoices(
    search: str | None = Query(None),
    status: InvoiceStatus | None = Query(None),
    client_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(ClientInvoice)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(ClientInvoice.invoice_number.ilike(like), ClientInvoice.service_description.ilike(like)))
    if status:
        q = q.filter(ClientInvoice.status == status)
    if client_id:
        q = q.filter(ClientInvoice.client_id == client_id)
    return q.order_by(ClientInvoice.created_at.desc()).all()


@router.post("", response_model=InvoiceDetail, status_code=201)
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_roles(UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER, UserRole.FINANCE_EXECUTIVE)
    ),
):
    if not db.get(Client, payload.client_id):
        raise HTTPException(404, "Client not found")
    data = payload.model_dump()
    data["invoice_number"] = data.get("invoice_number") or _next_invoice_number(db)
    if db.query(ClientInvoice).filter(ClientInvoice.invoice_number == data["invoice_number"]).first():
        raise HTTPException(409, "Invoice number already exists")
    inv = ClientInvoice(**data)
    _apply_financials(inv)
    db.add(inv)
    db.flush()
    log_action(db, user, "Created invoice", "ClientInvoice", inv.id, inv.invoice_number)
    db.commit()
    db.refresh(inv)
    return inv


@router.get("/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    inv = db.get(ClientInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


@router.patch("/{invoice_id}", response_model=InvoiceDetail)
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER, UserRole.FINANCE_EXECUTIVE)),
):
    inv = db.get(ClientInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")

    incoming = payload.model_dump(exclude_unset=True)
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
    inv = db.get(ClientInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status != InvoiceStatus.DRAFT:
        raise HTTPException(400, "Only draft invoices can be deleted")
    db.delete(inv)
    log_action(db, user, "Deleted draft invoice", "ClientInvoice", invoice_id)
    db.commit()


@router.post("/{invoice_id}/duplicate", response_model=InvoiceDetail, status_code=201)
def duplicate_invoice(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    src = db.get(ClientInvoice, invoice_id)
    if not src:
        raise HTTPException(404, "Invoice not found")
    inv = ClientInvoice(
        invoice_number=_next_invoice_number(db),
        client_id=src.client_id,
        invoice_date=src.invoice_date,
        due_date=src.due_date,
        service_description=src.service_description,
        taxable_value=src.taxable_value,
        gst_rate=src.gst_rate,
        is_interstate=src.is_interstate,
        tds_applicable=src.tds_applicable,
        tds_rate=src.tds_rate,
        status=InvoiceStatus.DRAFT,
    )
    _apply_financials(inv)
    db.add(inv)
    db.flush()
    log_action(db, user, "Duplicated invoice", "ClientInvoice", inv.id, f"from {src.invoice_number}")
    db.commit()
    db.refresh(inv)
    return inv


@router.post("/{invoice_id}/payments", response_model=InvoiceDetail, status_code=201)
def record_payment(
    invoice_id: int,
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER, UserRole.FINANCE_EXECUTIVE)),
):
    inv = db.get(ClientInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status in {InvoiceStatus.CANCELLED, InvoiceStatus.DRAFT}:
        raise HTTPException(400, "Cannot record payment on a draft or cancelled invoice")
    if payload.amount <= 0:
        raise HTTPException(400, "Payment amount must be positive")

    payment = Payment(invoice_id=inv.id, **payload.model_dump())
    db.add(payment)
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
    inv = db.get(ClientInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv.payments
