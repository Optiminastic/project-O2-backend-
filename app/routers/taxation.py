from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user
from app.models import ClientInvoice, VendorInvoice, User, GstStatus
from app.services.taxation import compute_gst, compute_tds

router = APIRouter(prefix="/taxation", tags=["taxation"])


@router.get("/gst/preview")
def gst_preview(taxable_value: float, gst_rate: float = 18.0, is_interstate: bool = False, user: User = Depends(get_current_user)):
    """Live GST calculator — used by the invoice form."""
    g = compute_gst(taxable_value, gst_rate, is_interstate)
    return g.__dict__


@router.get("/tds/preview")
def tds_preview(base_amount: float, tds_rate: float, applicable: bool = True, user: User = Depends(get_current_user)):
    return {"tds_amount": compute_tds(base_amount, tds_rate, applicable)}


@router.get("/summary")
def taxation_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """GST + TDS pendency overview across all invoices."""
    client_invoices = db.query(ClientInvoice).all()
    vendor_invoices = db.query(VendorInvoice).all()

    gst_by_status: dict[str, float] = {s.value: 0.0 for s in GstStatus}
    gst_collected = 0.0
    for inv in client_invoices:
        gst_by_status[inv.gst_status.value] = round(gst_by_status.get(inv.gst_status.value, 0.0) + inv.gst_amount, 2)
        gst_collected += inv.gst_amount

    client_tds_expected = round(sum(i.expected_tds for i in client_invoices), 2)
    vendor_tds_total = round(sum(i.tds_amount for i in vendor_invoices), 2)

    return {
        "gst_by_status": gst_by_status,
        "gst_total": round(gst_collected, 2),
        "client_tds_receivable": client_tds_expected,
        "vendor_tds_payable": vendor_tds_total,
        "client_invoice_count": len(client_invoices),
        "vendor_invoice_count": len(vendor_invoices),
    }


@router.get("/gst/pending")
def gst_pending(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Invoices whose GST is not yet reconciled."""
    rows = (
        db.query(ClientInvoice)
        .filter(ClientInvoice.gst_status != GstStatus.RECONCILED)
        .order_by(ClientInvoice.invoice_date.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "invoice_number": r.invoice_number,
            "taxable_value": r.taxable_value,
            "gst_amount": r.gst_amount,
            "gst_status": r.gst_status.value,
        }
        for r in rows
    ]
