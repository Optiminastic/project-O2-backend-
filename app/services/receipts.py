"""Recording money received from a client against issued tax invoices.

One bank receipt may pay several invoices of the same client. It is kept as one
``Payment`` row per invoice, so every invoice's balance and lock logic stays
the same; the rows of one receipt share a ``receipt_ref``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import ClientInvoice, CompanyBank, InvoiceStatus, Payment, PaymentMode
from app.services.invoice_lock import SETTLEMENT_TOLERANCE, recompute_invoice, settled_amount
from app.services.invoice_workflow import format_inr


@dataclass(frozen=True)
class ReceiptDetails:
    """What one bank receipt says, whichever invoices it pays."""

    payment_date: date
    bank_name: str
    bank_account_id: int | None = None
    bank_reference: str | None = None
    remarks: str | None = None
    attachment: str | None = None


def receiving_bank_name(db: Session, bank_account_id: int | None, typed_name: str | None) -> str:
    """The bank as the receipt records it: one of ours by id, or the name typed."""
    if bank_account_id is None:
        return typed_name or ""
    bank = db.get(CompanyBank, bank_account_id)
    if bank is None:
        raise HTTPException(404, "Bank not found")
    if not bank.is_active:
        raise HTTPException(400, f"{bank.label} is deactivated. Choose another bank.")
    return bank.label


def apply_receipt(
    inv: ClientInvoice,
    details: ReceiptDetails,
    amount: float,
    *,
    tds_deducted: float | None = None,
    gst_component: float = 0.0,
    receipt_ref: str | None = None,
) -> Payment:
    """Record ``amount`` against ``inv`` after checking it fits what is still due. The caller commits."""
    # A receipt across several invoices names which one a problem is on.
    on = f"{inv.display_number}: " if receipt_ref else ""
    if not inv.is_issued or inv.status == InvoiceStatus.CANCELLED:
        raise HTTPException(400, f"{on}Receipts can only be recorded against an issued, active tax invoice")
    if inv.invoice_date and details.payment_date < inv.invoice_date:
        raise HTTPException(
            400, f"{on}The receipt date cannot be before the invoice date ({inv.invoice_date:%d %b %Y})."
        )
    if gst_component > amount:
        raise HTTPException(400, f"{on}The GST component cannot be more than the amount received.")
    remaining = round(inv.total_amount - settled_amount(inv), 2)
    tds_so_far = round(sum(p.tds_deducted or 0.0 for p in inv.payments), 2)
    tds = tds_deducted
    if tds is None:
        tds = tds_with_receipt(remaining, amount, round(max(inv.expected_tds - tds_so_far, 0.0), 2))
    settling = round(amount + tds, 2)
    if settling > remaining + SETTLEMENT_TOLERANCE:
        raise HTTPException(
            400,
            f"{on}Receipt of {format_inr(amount)} plus TDS of {format_inr(tds)} "
            f"is more than the {format_inr(remaining)} still due on this invoice.",
        )
    if tds_so_far + tds > inv.expected_tds + SETTLEMENT_TOLERANCE:
        raise HTTPException(
            400,
            f"{on}TDS of {format_inr(tds_so_far + tds)} is more than the "
            f"{format_inr(inv.expected_tds)} expected at {inv.tds_rate:g}%. Check the amount the client deducted.",
        )

    payment = Payment(
        payment_mode=PaymentMode.BANK,
        amount=amount,
        payment_date=details.payment_date,
        bank_name=details.bank_name,
        bank_account_id=details.bank_account_id,
        bank_reference=details.bank_reference,
        tds_deducted=tds,
        gst_component=gst_component,
        remarks=details.remarks,
        attachment=details.attachment,
        receipt_ref=receipt_ref,
    )
    # Through the relationship, so the payments already loaded for the checks above include it.
    inv.payments.append(payment)
    # This locks the invoice and recomputes status.
    recompute_invoice(inv)
    return payment


def tds_with_receipt(remaining: float, amount: float, tds_left: float) -> float:
    """TDS counted with a receipt when the form does not ask for it.

    Clients pay net of TDS, so once the cash clears everything but the TDS still
    expected, that TDS is taken as deducted. Earlier part payments count none.
    """
    if amount < remaining - tds_left - SETTLEMENT_TOLERANCE:
        return 0.0
    return round(min(tds_left, max(remaining - amount, 0.0)), 2)


def new_receipt_ref() -> str:
    """Shared by the payment rows of one receipt across several invoices."""
    return uuid.uuid4().hex
