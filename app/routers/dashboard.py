from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user
from app.models import (
    Client,
    Vendor,
    ClientInvoice,
    PaymentApproval,
    VendorInvoice,
    BankTransaction,
    User,
    ApprovalStatus,
    VerificationStatus,
    GstStatus,
)
from app.schemas.misc import DashboardSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

PENDING_APPROVAL_STATES = {
    ApprovalStatus.SUBMITTED_CFO,
    ApprovalStatus.SUBMITTED_CEO,
    ApprovalStatus.CFO_APPROVED,
    ApprovalStatus.PAYMENT_READY,
}


@router.get("/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    invoices = db.query(ClientInvoice).all()
    approvals = db.query(PaymentApproval).all()
    vendor_invoices = db.query(VendorInvoice).all()
    txns = db.query(BankTransaction).all()

    net_receivable = round(sum(i.amount_pending for i in invoices), 2)
    net_payable = round(sum(vi.net_payable for vi in vendor_invoices), 2)
    gst_pending = round(
        sum(i.gst_amount for i in invoices if i.gst_status != GstStatus.RECONCILED), 2
    )
    pending_approvals = sum(1 for a in approvals if a.status in PENDING_APPROVAL_STATES)

    reconciled = sum(1 for t in txns if t.verification_status == VerificationStatus.RECONCILED)
    matched = sum(
        1
        for t in txns
        if t.verification_status
        in {VerificationStatus.AUTO_MATCHED, VerificationStatus.MANUALLY_MATCHED, VerificationStatus.RECONCILED}
    )
    reconciliation_rate = round((matched / len(txns) * 100.0), 2) if txns else 0.0

    recent = sorted(invoices, key=lambda i: i.created_at, reverse=True)[:6]
    recent_invoices = [
        {
            "id": i.id,
            "invoice_number": i.invoice_number,
            "client_id": i.client_id,
            "total_amount": i.total_amount,
            "amount_pending": i.amount_pending,
            "status": i.status.value,
            "is_locked": i.is_locked,
        }
        for i in recent
    ]

    queue = [a for a in approvals if a.status in PENDING_APPROVAL_STATES][:6]
    approvals_queue = [
        {
            "id": a.id,
            "payee_name": a.payee_name,
            "amount": a.amount,
            "net_payable": a.net_payable,
            "status": a.status.value,
        }
        for a in queue
    ]

    # Monthly cashflow from received payments (by invoice payments).
    buckets: dict[str, float] = defaultdict(float)
    for inv in invoices:
        for p in inv.payments:
            key = p.payment_date.strftime("%b")
            buckets[key] += p.amount
    month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    cashflow = [
        {"label": m, "value": round(buckets[m], 2)} for m in month_order if m in buckets
    ]
    if not cashflow:
        cashflow = [{"label": m, "value": 0} for m in month_order[:6]]

    return DashboardSummary(
        total_clients=db.query(Client).count(),
        total_vendors=db.query(Vendor).count(),
        total_invoices=len(invoices),
        net_receivable=net_receivable,
        net_payable=net_payable,
        pending_approvals=pending_approvals,
        gst_pending=gst_pending,
        reconciliation_rate=reconciliation_rate,
        recent_invoices=recent_invoices,
        approvals_queue=approvals_queue,
        cashflow=cashflow,
    )
