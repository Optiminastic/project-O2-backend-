"""GST and TDS automation."""

from dataclasses import dataclass


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
