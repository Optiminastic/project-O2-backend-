"""GST and TDS automation."""

from dataclasses import dataclass
from datetime import date

# Clients deduct TDS on professional fees under section 194J at 10%. TDS always
# applies to client invoices; only the rate is set per invoice.
DEFAULT_CLIENT_TDS_RATE = 10.0


@dataclass
class GstBreakdown:
    taxable_value: float
    gst_rate: float
    gst_amount: float
    cgst: float
    sgst: float
    igst: float
    total: float


def compute_gst(taxable_value: float, gst_rate: float, is_interstate: bool) -> GstBreakdown:
    """Compute GST with CGST/SGST split for intra-state, IGST for inter-state."""
    gst_amount = round(taxable_value * gst_rate / 100.0, 2)
    if is_interstate:
        igst = gst_amount
        cgst = sgst = 0.0
    else:
        igst = 0.0
        cgst = sgst = round(gst_amount / 2.0, 2)
    total = round(taxable_value + gst_amount, 2)
    return GstBreakdown(
        taxable_value=round(taxable_value, 2),
        gst_rate=gst_rate,
        gst_amount=gst_amount,
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        total=total,
    )


def compute_tds(base_amount: float, tds_rate: float, applicable: bool) -> float:
    """TDS amount deducted on a base value."""
    if not applicable or tds_rate <= 0:
        return 0.0
    return round(base_amount * tds_rate / 100.0, 2)


def vendor_net_payable(invoice_amount: float, gst_amount: float, tds_amount: float) -> float:
    """Net payable to a vendor = gross + GST - TDS."""
    return round(invoice_amount + gst_amount - tds_amount, 2)


# ---------- vendor side ----------

# GST slabs a vendor bill can carry.
VENDOR_GST_RATES = (0.0, 5.0, 12.0, 18.0, 28.0)


@dataclass(frozen=True)
class TdsSection:
    section: str
    label: str
    rate: float


# TDS we deduct from vendor bills, keyed by what the form sends.
TDS_SECTIONS: dict[str, TdsSection] = {
    "194C_INDIVIDUAL": TdsSection("194C", "194C contractor (individual / HUF)", 1.0),
    "194C_FIRM": TdsSection("194C", "194C contractor (company / firm)", 2.0),
    "194J_TECHNICAL": TdsSection("194J", "194J technical services", 2.0),
    "194J_PROFESSIONAL": TdsSection("194J", "194J professional fees", 10.0),
    "194H": TdsSection("194H", "194H commission", 2.0),
    "NONE": TdsSection("", "No TDS", 0.0),
}
DEFAULT_VENDOR_TDS_SECTION = "194C_FIRM"

# TDS is due to the government by the 7th of the next month; March deductions by 30 April.
TDS_DUE_DAY = 7
MARCH_TDS_DUE = (4, 30)


def tds_deposit_due_date(deducted_on: date) -> date:
    if deducted_on.month == 3:
        return date(deducted_on.year, *MARCH_TDS_DUE)
    if deducted_on.month == 12:
        return date(deducted_on.year + 1, 1, TDS_DUE_DAY)
    return date(deducted_on.year, deducted_on.month + 1, TDS_DUE_DAY)
