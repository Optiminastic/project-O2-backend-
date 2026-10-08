"""Invoice locking + payment-driven status recomputation."""

from datetime import datetime, timezone

from app.models import ClientInvoice, InvoiceStatus

# Critical financial fields that may not change once an invoice is locked.
LOCKED_FIELDS = {
    "client_id",
    "agent_id",  # commission is earned on the issued invoice
    "service_id",
    "service_title",
    "sac_code",
    "total_amount",
    "taxable_value",
    "gst_amount",
    "gst_rate",
    "cgst",
    "sgst",
    "igst",
    "tds_rate",
    "expected_tds",
    "invoice_date",
    "service_description",
}


# Paise left over from rounding never keep an invoice open.
SETTLEMENT_TOLERANCE = 0.01


def settled_amount(invoice: ClientInvoice) -> float:
    """Cash received plus the TDS the client deducted: TDS is paid to the government on our behalf."""
    return round(sum(p.amount + (p.tds_deducted or 0.0) for p in invoice.payments), 2)


def recompute_invoice(invoice: ClientInvoice) -> None:
    """Recompute received/pending amounts, payment status and lock state from payments."""
    received = round(sum(p.amount for p in invoice.payments), 2)  # cash only
    settled = settled_amount(invoice)
    invoice.amount_received = received
    invoice.amount_pending = round(max(invoice.total_amount - settled, 0.0), 2)

    if received <= 0:
        # Keep an explicit Draft/Sent/Pending status if no payment yet.
        if invoice.status in {InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.FULLY_PAID}:
            invoice.status = InvoiceStatus.PENDING
        return

    # A payment exists -> lock the invoice.
    if not invoice.is_locked:
        invoice.is_locked = True
        invoice.locked_at = datetime.now(timezone.utc)

    if settled >= invoice.total_amount - SETTLEMENT_TOLERANCE:
        invoice.status = InvoiceStatus.FULLY_PAID
    else:
        invoice.status = InvoiceStatus.PARTIALLY_PAID


def assert_editable(invoice: ClientInvoice, incoming: dict) -> list[str]:
    """Return the list of locked fields the caller is attempting to change. Empty = OK."""
    if not invoice.is_locked:
        return []
    violations = []
    for field in LOCKED_FIELDS:
        if field in incoming and incoming[field] is not None:
            current = getattr(invoice, field)
            if incoming[field] != current:
                violations.append(field)
    return violations
