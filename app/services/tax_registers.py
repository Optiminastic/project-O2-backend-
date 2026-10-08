"""The four tax registers and the monthly tax position built from them.

* GST on sales: output GST on our issued tax invoices, by invoice date.
* GST on purchases: GST on vendor bills; claimable as input credit (ITC) only
  when the vendor's GSTIN was captured.
* TDS by clients: what clients deducted from their payments to us, by payment
  date, with the Form 16A certificate that lets us claim it.
* TDS on vendors: what we deducted from vendor bills, by bill date, with its
  deposit to the government (due the 7th of the next month).

The Taxation page and the dashboard both read from here, so they always agree.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from app.core.period import Period
from app.models import ClientInvoice, InvoiceStatus, Payment, PROFORMA_STAGES, VendorInvoice
from app.schemas.taxation import (
    ClientTdsRow,
    GstTotals,
    PurchaseGstRow,
    SalesGstRow,
    TaxSummary,
    VendorTdsRow,
)
from app.services.numbering import business_date
from app.services.taxation import TDS_SECTIONS, tds_deposit_due_date

# A cancelled tax invoice carries no GST; it is reversed rather than paid.
EXCLUDED_SALE_STATUSES = PROFORMA_STAGES | {InvoiceStatus.CANCELLED}


def _r(value: float) -> float:
    return round(value, 2)


# ---------- GST on sales ----------

def _sales(db: Session, period: Period | None) -> list[ClientInvoice]:
    q = (
        db.query(ClientInvoice)
        .options(joinedload(ClientInvoice.client))
        .filter(ClientInvoice.status.notin_(EXCLUDED_SALE_STATUSES), ClientInvoice.invoice_date.isnot(None))
    )
    if period:
        q = q.filter(period.dates(ClientInvoice.invoice_date))
    return q.order_by(ClientInvoice.invoice_date.desc(), ClientInvoice.id.desc()).all()


def sales_register(db: Session, period: Period | None) -> list[SalesGstRow]:
    return [
        SalesGstRow(
            invoice_id=i.id,
            invoice_number=i.invoice_number,
            invoice_date=i.invoice_date,
            client_name=i.client.business_name,
            client_gstin=i.client.gst_number,
            taxable_value=i.taxable_value,
            gst_rate=i.gst_rate,
            cgst=i.cgst,
            sgst=i.sgst,
            igst=i.igst,
            gst_amount=i.gst_amount,
            total_amount=i.total_amount,
        )
        for i in _sales(db, period)
    ]


# ---------- GST on purchases ----------

def _purchases(db: Session, period: Period | None) -> list[VendorInvoice]:
    q = db.query(VendorInvoice).options(joinedload(VendorInvoice.vendor))
    if period:
        q = q.filter(period.dates(VendorInvoice.invoice_date))
    return q.order_by(VendorInvoice.invoice_date.desc(), VendorInvoice.id.desc()).all()


def _itc_eligible(bill: VendorInvoice) -> bool:
    return bool(bill.vendor_gstin) and bill.gst_amount > 0


def purchase_register(db: Session, period: Period | None) -> list[PurchaseGstRow]:
    return [
        PurchaseGstRow(
            vendor_invoice_id=b.id,
            vendor_id=b.vendor_id,
            invoice_number=b.invoice_number,
            invoice_date=b.invoice_date,
            vendor_name=b.vendor.business_name,
            vendor_gstin=b.vendor_gstin,
            taxable_value=b.invoice_amount,
            gst_rate=b.gst_rate,
            cgst=b.cgst,
            sgst=b.sgst,
            igst=b.igst,
            gst_amount=b.gst_amount,
            itc_eligible=_itc_eligible(b),
        )
        for b in _purchases(db, period)
    ]


# ---------- TDS deducted by clients ----------

def _client_tds(db: Session, period: Period | None) -> list[Payment]:
    q = (
        db.query(Payment)
        .options(joinedload(Payment.invoice).joinedload(ClientInvoice.client))
        .filter(Payment.tds_deducted > 0)
    )
    if period:
        q = q.filter(period.dates(Payment.payment_date))
    return q.order_by(Payment.payment_date.desc(), Payment.id.desc()).all()


def client_tds_row(p: Payment) -> ClientTdsRow:
    return ClientTdsRow(
        payment_id=p.id,
        payment_date=p.payment_date,
        invoice_id=p.invoice_id,
        invoice_number=p.invoice.display_number,
        client_name=p.invoice.client.business_name,
        tds_deducted=p.tds_deducted,
        certificate_number=p.tds_certificate_number,
        certificate_date=p.tds_certificate_date,
    )


def client_tds_register(db: Session, period: Period | None) -> list[ClientTdsRow]:
    return [client_tds_row(p) for p in _client_tds(db, period)]


# ---------- TDS we deduct from vendors ----------

def vendor_tds_row(b: VendorInvoice) -> VendorTdsRow:
    due = tds_deposit_due_date(b.invoice_date)
    section = TDS_SECTIONS.get(b.tds_section or "")
    return VendorTdsRow(
        vendor_invoice_id=b.id,
        vendor_id=b.vendor_id,
        invoice_number=b.invoice_number,
        invoice_date=b.invoice_date,
        vendor_name=b.vendor.business_name,
        vendor_pan=b.vendor.pan,
        section_label=section.label if section else "Section not recorded",
        tds_rate=b.tds_rate,
        tds_amount=b.tds_amount,
        due_date=due,
        deposited_on=b.tds_deposited_on,
        challan_number=b.tds_challan_number,
        overdue=b.tds_deposited_on is None and business_date() > due,
    )


def vendor_tds_register(db: Session, period: Period | None) -> list[VendorTdsRow]:
    return [vendor_tds_row(b) for b in _purchases(db, period) if b.tds_amount > 0]


# ---------- the month's position ----------

def _gst_totals(rows) -> GstTotals:
    return GstTotals(
        taxable=_r(sum(r.taxable_value for r in rows)),
        cgst=_r(sum(r.cgst or 0 for r in rows)),
        sgst=_r(sum(r.sgst or 0 for r in rows)),
        igst=_r(sum(r.igst or 0 for r in rows)),
        total=_r(sum(r.gst_amount for r in rows)),
    )


def net_gst(output_total: float, input_total: float) -> tuple[float, float]:
    """(GST to pay, input credit carried forward) after setting input credit against output GST."""
    difference = _r(output_total - input_total)
    return (difference, 0.0) if difference >= 0 else (0.0, -difference)


def tax_summary(db: Session, period: Period | None) -> TaxSummary:
    sales = sales_register(db, period)
    purchases = purchase_register(db, period)
    eligible = [p for p in purchases if p.itc_eligible]
    client_tds = client_tds_register(db, period)
    vendor_tds = vendor_tds_register(db, period)

    output_gst, input_gst = _gst_totals(sales), _gst_totals(eligible)
    payable, carried = net_gst(output_gst.total, input_gst.total)
    deposited = _r(sum(r.tds_amount for r in vendor_tds if r.deposited_on))
    deducted = _r(sum(r.tds_amount for r in vendor_tds))
    return TaxSummary(
        output_gst=output_gst,
        input_gst=input_gst,
        input_gst_ineligible=_r(sum(p.gst_amount for p in purchases if not p.itc_eligible)),
        net_gst_payable=payable,
        gst_credit_carried=carried,
        sales_count=len(sales),
        purchase_count=len(purchases),
        client_tds_deducted=_r(sum(r.tds_deducted for r in client_tds)),
        client_tds_certificates_pending=sum(1 for r in client_tds if not r.certificate_number),
        vendor_tds_deducted=deducted,
        vendor_tds_deposited=deposited,
        vendor_tds_to_deposit=_r(deducted - deposited),
        vendor_tds_overdue_count=sum(1 for r in vendor_tds if r.overdue),
    )
