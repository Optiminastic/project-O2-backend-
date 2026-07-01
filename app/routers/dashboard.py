from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user
from app.models import (
    Client,
    Vendor,
    ClientInvoice,
    Payment,
    PaymentApproval,
    VendorInvoice,
    BankTransaction,
    User,
    ApprovalStatus,
    VerificationStatus,
    GstStatus,
)
from app.schemas.misc import DashboardSummary, CashflowPoint

_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

PENDING_APPROVAL_STATES = {
    ApprovalStatus.SUBMITTED_CEO,
    ApprovalStatus.PAYMENT_READY,
}


@router.get("/revenue", response_model=list[CashflowPoint])
def revenue(
    range_: str = Query("monthly", alias="range"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Real received-revenue series, bucketed by week / month / year from actual payments."""
    payments = db.query(Payment).all()
    today = date.today()
    rng = range_.lower()

    if rng == "weekly":
        # Last 8 weeks (oldest → newest), keyed by each date's Monday.
        this_monday = today - timedelta(days=today.weekday())
        buckets = [0.0] * 8
        for p in payments:
            pm = p.payment_date - timedelta(days=p.payment_date.weekday())
            idx = 7 - ((this_monday - pm).days // 7)
            if 0 <= idx < 8:
                buckets[idx] += p.amount
        return [CashflowPoint(label=f"W{i + 1}", value=round(buckets[i], 2)) for i in range(8)]

    if rng == "yearly":
        buckets = [0.0] * 5
        for p in payments:
            idx = 4 - (today.year - p.payment_date.year)
            if 0 <= idx < 5:
                buckets[idx] += p.amount
        years = [today.year - (4 - i) for i in range(5)]
        return [CashflowPoint(label=str(years[i]), value=round(buckets[i], 2)) for i in range(5)]

    # Monthly (default) — last 12 months.
    seq: list[tuple[int, int]] = []
    for back in range(11, -1, -1):
        mm, yy = today.month - back, today.year
        while mm <= 0:
            mm += 12
            yy -= 1
        seq.append((yy, mm))
    idxmap = {key: i for i, key in enumerate(seq)}
    buckets = [0.0] * 12
    for p in payments:
        key = (p.payment_date.year, p.payment_date.month)
        if key in idxmap:
            buckets[idxmap[key]] += p.amount
    return [CashflowPoint(label=_MONTH_ABBR[seq[i][1]], value=round(buckets[i], 2)) for i in range(12)]


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
