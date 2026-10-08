from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import require_roles
from app.core.rbac import NON_EXEC
from app.models import ClientInvoice, VendorInvoice, Payment, User
from app.schemas.misc import PaymentReceiptOut
from app.schemas.taxation import (
    CertificateIn,
    ClientTdsRow,
    DepositIn,
    PurchaseGstRow,
    SalesGstRow,
    TaxSummary,
    VendorTdsRow,
)
from app.services import tax_registers as registers
from app.services.audit import log_action
from app.services.numbering import business_date
from app.services.taxation import compute_gst, compute_tds
from app.core.period import Period, month_period

router = APIRouter(prefix="/taxation", tags=["taxation"])


@router.get("/gst/preview")
def gst_preview(taxable_value: float, gst_rate: float = 18.0, is_interstate: bool = False, user: User = Depends(require_roles(*NON_EXEC))):
    """Live GST calculator — used by the invoice form."""
    g = compute_gst(taxable_value, gst_rate, is_interstate)
    return g.__dict__


@router.get("/tds/preview")
def tds_preview(base_amount: float, tds_rate: float, applicable: bool = True, user: User = Depends(require_roles(*NON_EXEC))):
    return {"tds_amount": compute_tds(base_amount, tds_rate, applicable)}


@router.get("/summary", response_model=TaxSummary)
def taxation_summary(
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    """The month's tax position: GST to pay after input credit, and TDS on both sides."""
    return registers.tax_summary(db, period)


@router.get("/gst/sales", response_model=list[SalesGstRow])
def gst_sales(
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    return registers.sales_register(db, period)


@router.get("/gst/purchases", response_model=list[PurchaseGstRow])
def gst_purchases(
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    return registers.purchase_register(db, period)


@router.get("/tds/clients", response_model=list[ClientTdsRow])
def tds_by_clients(
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    return registers.client_tds_register(db, period)


@router.get("/tds/vendors", response_model=list[VendorTdsRow])
def tds_on_vendors(
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    return registers.vendor_tds_register(db, period)


@router.patch("/client-tds/{payment_id}/certificate", response_model=ClientTdsRow)
def record_tds_certificate(
    payment_id: int,
    payload: CertificateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    """Record (or clear) the client's Form 16A for the TDS deducted from this payment."""
    payment = db.get(Payment, payment_id)
    if not payment or payment.tds_deducted <= 0:
        raise HTTPException(404, "No TDS was deducted from this payment")
    payment.tds_certificate_number = payload.certificate_number
    payment.tds_certificate_date = payload.certificate_date
    action = "Recorded TDS certificate" if payload.certificate_number else "Cleared TDS certificate"
    log_action(db, user, action, "ClientInvoice", payment.invoice_id, payload.certificate_number)
    db.commit()
    return registers.client_tds_row(payment)


@router.patch("/vendor-tds/{vendor_invoice_id}/deposit", response_model=VendorTdsRow)
def record_tds_deposit(
    vendor_invoice_id: int,
    payload: DepositIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    """Record (or clear) the deposit of TDS deducted from a vendor bill."""
    bill = db.get(VendorInvoice, vendor_invoice_id)
    if not bill or bill.tds_amount <= 0:
        raise HTTPException(404, "No TDS was deducted from this bill")
    if payload.deposited_on and payload.deposited_on > business_date():
        raise HTTPException(400, "The deposit date cannot be in the future.")
    if payload.deposited_on and payload.deposited_on < bill.invoice_date:
        raise HTTPException(400, "The deposit date is before the bill date.")
    bill.tds_deposited_on = payload.deposited_on
    bill.tds_challan_number = payload.challan_number
    action = "Recorded TDS deposit" if payload.challan_number else "Cleared TDS deposit"
    log_action(db, user, action, "VendorInvoice", bill.id, payload.challan_number)
    db.commit()
    return registers.vendor_tds_row(bill)


@router.get("/receipts", response_model=list[PaymentReceiptOut])
def receipts(
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    """Ledger of client payments received (by payment date), newest first."""
    q = db.query(Payment).join(ClientInvoice, Payment.invoice_id == ClientInvoice.id)
    if period:
        q = q.filter(period.dates(Payment.payment_date))
    rows = q.order_by(Payment.payment_date.desc(), Payment.id.desc()).all()
    return [
        PaymentReceiptOut(
            id=p.id,
            invoice_id=p.invoice_id,
            invoice_number=p.invoice.invoice_number,
            client_id=p.invoice.client_id,
            amount=p.amount,
            payment_date=p.payment_date,
            payment_mode=p.payment_mode,
            bank_name=p.bank_name,
            bank_reference=p.bank_reference,
            tds_deducted=p.tds_deducted,
            gst_component=p.gst_component,
            remarks=p.remarks,
        )
        for p in rows
    ]
