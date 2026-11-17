"""Vendor payouts: paying vendor bills, split equally over their service lines.

What a vendor is owed on a bill is its ``amount_payable`` (net payable less
Optiminastic's commission). A bill with several service lines (UPS codes)
owes each line its share of that, in proportion to the line's amount.

A payout names a vendor, the bills it pays and an amount. The amount cannot be
more than those bills still owe, and it is split equally across every service
line on them: no line gets more than it still owes, and whatever a line cannot
take is shared among the rest. Money counts as committed once a payout is
raised (so two payouts cannot pay the same bill twice) and as paid once the
payout is released, when the bills' status moves to Partially / Fully Paid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ApprovalStatus, InvoiceStatus, PaymentApproval, PayoutAllocation, User, Vendor, VendorInvoice

# Money in a payout that is not (or no longer) on its way to the vendor.
NOT_COMMITTED = (ApprovalStatus.CFO_REJECTED, ApprovalStatus.CEO_REJECTED)
PAID = (ApprovalStatus.PAYMENT_RELEASED, ApprovalStatus.PAYMENT_VERIFIED)


def _paise(rupees: float) -> int:
    return int(round(rupees * 100))


def _rupees(paise: int) -> float:
    return round(paise / 100, 2)


@dataclass
class LineDue:
    """What one service line of a bill is owed. ``line_id`` is None for a bill without lines."""

    invoice_id: int
    line_id: int | None
    code: str | None
    service_title: str | None
    payable: float
    committed: float = 0.0
    paid: float = 0.0

    @property
    def due(self) -> float:
        return round(self.payable - self.committed, 2)


@dataclass
class BillDue:
    invoice: VendorInvoice
    lines: list[LineDue] = field(default_factory=list)

    @property
    def payable(self) -> float:
        return round(self.invoice.amount_payable, 2)

    @property
    def committed(self) -> float:
        return round(sum(line.committed for line in self.lines), 2)

    @property
    def paid(self) -> float:
        return round(sum(line.paid for line in self.lines), 2)

    @property
    def due(self) -> float:
        return round(self.payable - self.committed, 2)


def line_payables(invoice: VendorInvoice) -> list[tuple[int | None, int]]:
    """(line id, paise owed) per service line: the bill's payable shared by line amount, exact to the paisa."""
    total = _paise(invoice.amount_payable)
    if not invoice.lines or invoice.invoice_amount <= 0:
        return [(None, total)]
    out, given = [], 0
    for i, line in enumerate(invoice.lines):
        share = total - given if i == len(invoice.lines) - 1 else int(total * line.amount / invoice.invoice_amount)
        out.append((line.id, share))
        given += share
    return out


def _allocated(db: Session, invoice_ids: list[int], statuses_out: tuple, exclude_approval: int | None = None):
    """Allocations per (invoice, line) on payouts whose status is not in ``statuses_out``."""
    q = (
        db.query(PayoutAllocation.vendor_invoice_id, PayoutAllocation.line_id, func.sum(PayoutAllocation.amount))
        .join(PaymentApproval, PaymentApproval.id == PayoutAllocation.approval_id)
        .filter(PayoutAllocation.vendor_invoice_id.in_(invoice_ids), PaymentApproval.status.notin_(statuses_out))
    )
    if exclude_approval is not None:
        q = q.filter(PaymentApproval.id != exclude_approval)
    return {(i, l): _paise(total or 0) for i, l, total in q.group_by(PayoutAllocation.vendor_invoice_id, PayoutAllocation.line_id)}


def bill_dues(db: Session, invoices: list[VendorInvoice], exclude_approval: int | None = None) -> list[BillDue]:
    """What each bill (and each of its lines) is owed, committed to payouts and already paid."""
    ids = [i.id for i in invoices]
    if not ids:
        return []
    committed = _allocated(db, ids, NOT_COMMITTED, exclude_approval)
    paid_statuses = tuple(s for s in ApprovalStatus if s not in PAID)
    paid = _allocated(db, ids, paid_statuses, exclude_approval)
    out = []
    for inv in invoices:
        lines_by_id = {line.id: line for line in inv.lines}
        bill = BillDue(invoice=inv)
        payables = line_payables(inv)
        for line_id, owed in payables:
            line = lines_by_id.get(line_id)
            c, p = committed.get((inv.id, line_id), 0), paid.get((inv.id, line_id), 0)
            if line_id is not None and len(payables) and (inv.id, None) in committed:
                # Money paid against the whole bill before it had lines is shared by line.
                c += int(committed[(inv.id, None)] * owed / max(_paise(inv.amount_payable), 1))
                p += int(paid.get((inv.id, None), 0) * owed / max(_paise(inv.amount_payable), 1))
            bill.lines.append(LineDue(
                invoice_id=inv.id, line_id=line_id, code=line.code if line else None,
                service_title=line.service_title if line else None,
                payable=_rupees(owed), committed=_rupees(c), paid=_rupees(p),
            ))
        out.append(bill)
    return out


def split_equally(amount: float, dues: list[tuple[object, float]]) -> dict[object, float]:
    """Share ``amount`` equally over ``dues`` (key, owed), never giving a key more than it owes.

    Water-filling in paise: the smallest debts are settled first and their unused
    share goes to the rest, so the split is as even as the debts allow. Every
    paisa of ``amount`` is placed; it must not exceed the total owed.
    """
    remaining = _paise(amount)
    open_ = sorted(((key, _paise(owed)) for key, owed in dues if _paise(owed) > 0), key=lambda kv: kv[1])
    if remaining > sum(o for _, o in open_):
        raise ValueError("amount exceeds what is owed")
    given: dict[object, int] = {}
    for i, (key, owed) in enumerate(open_):
        share = remaining // (len(open_) - i)
        take = min(owed, share)
        given[key] = take
        remaining -= take
    # Paise left by rounding down go one each to lines with room, in order.
    for key, owed in open_:
        if remaining <= 0:
            break
        if given[key] < owed:
            given[key] += 1
            remaining -= 1
    return {key: _rupees(p) for key, p in given.items() if p > 0}


def split_oldest_first(amount: float, bills: list[BillDue]) -> dict[tuple[int, int | None], float]:
    """Pay the oldest bill first (by bill date, then id), each line in turn, until ``amount`` runs out."""
    remaining = _paise(amount)
    given: dict[tuple[int, int | None], float] = {}
    for bill in sorted(bills, key=lambda b: (b.invoice.invoice_date, b.invoice.id)):
        for line in bill.lines:
            if remaining <= 0:
                return given
            take = min(remaining, max(_paise(line.due), 0))
            if take > 0:
                given[(line.invoice_id, line.line_id)] = _rupees(take)
                remaining -= take
    return given


def create_vendor_payout(
    db: Session, user: User, vendor: Vendor, invoice_ids: list[int], amount: float,
    purpose: str | None, bank_details: str | None, split: str = "services",
) -> PaymentApproval:
    """A payout to ``vendor`` of ``amount`` over the given bills.

    ``split="services"`` divides it equally across their service lines; ``"oldest"`` pays
    the oldest bill first.
    """
    if len(invoice_ids) != len(set(invoice_ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A bill is selected twice.")
    invoices = db.query(VendorInvoice).filter(VendorInvoice.id.in_(invoice_ids)).all()
    if len(invoices) != len(invoice_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor invoice not found")
    if any(i.vendor_id != vendor.id for i in invoices):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Every bill must be from {vendor.business_name}.")

    dues = bill_dues(db, invoices)
    total_due = round(sum(b.due for b in dues), 2)
    if total_due <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing is left to pay on the selected bills.")
    if _paise(amount) > _paise(total_due):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The payment of ₹{amount:,.2f} is more than the ₹{total_due:,.2f} still due on the selected bills.",
        )
    if split == "oldest":
        shares = split_oldest_first(amount, dues)
    else:
        shares = split_equally(amount, [((line.invoice_id, line.line_id), line.due) for b in dues for line in b.lines])

    numbers = ", ".join(i.invoice_number for i in invoices)
    approval = PaymentApproval(
        payee_name=vendor.business_name,
        vendor_id=vendor.id,
        vendor_invoice_id=invoices[0].id if len(invoices) == 1 else None,
        amount=amount,
        # TDS was already taken off each bill's net payable, so the payout is cash in full.
        tax_deductions=0.0,
        net_payable=amount,
        purpose=purpose or f"Vendor payout · {numbers}",
        bank_details=bank_details,
        requested_by_id=user.id,
    )
    approval.allocations = [
        PayoutAllocation(vendor_invoice_id=invoice_id, line_id=line_id, amount=share)
        for (invoice_id, line_id), share in shares.items()
    ]
    db.add(approval)
    return approval


def assert_still_within_due(db: Session, approval: PaymentApproval) -> None:
    """Resubmitting a rejected payout must still fit what its bills owe now."""
    if not approval.allocations:
        return
    invoices = list({a.invoice for a in approval.allocations})
    dues = {(line.invoice_id, line.line_id): line.due
            for b in bill_dues(db, invoices, exclude_approval=approval.id) for line in b.lines}
    for a in approval.allocations:
        if _paise(a.amount) > _paise(dues.get((a.vendor_invoice_id, a.line_id), 0)):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Bill {a.invoice.invoice_number} has since been paid by another payout. Raise a new payout instead.",
            )


def refresh_bill_status(db: Session, approval: PaymentApproval) -> None:
    """After a payout is released, mark its bills Partially or Fully Paid."""
    invoices = list({a.invoice for a in approval.allocations})
    for bill in bill_dues(db, invoices):
        if bill.paid <= 0:
            continue
        bill.invoice.status = InvoiceStatus.FULLY_PAID if _paise(bill.paid) >= _paise(bill.payable) else InvoiceStatus.PARTIALLY_PAID
