"""Invoice lines: one per service the project bills, and the invoice totals they add up to."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status

from app.config import settings
from app.models import Client, ClientInvoice, InvoiceLine, Project
from app.services.taxation import compute_gst, compute_tds, is_interstate_supply


@dataclass(frozen=True)
class LineInput:
    service_id: int
    taxable_value: float
    description: str | None = None


def primary_service_id(project: Project) -> int:
    """The service a single-amount invoice bills: the project's first service."""
    ids = [s.id for s in project.services]
    if project.service_id in ids:
        return project.service_id
    return ids[0]


def build_lines(project: Project, inputs: list[LineInput]) -> list[InvoiceLine]:
    """Lines for ``inputs``, each copying its service's title, SAC code and GST rate."""
    allowed = {s.id: s for s in project.services}
    seen: set[int] = set()
    lines = []
    for position, item in enumerate(inputs):
        service = allowed.get(item.service_id)
        if service is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"That service is not one of project {project.code}'s services."
            )
        if item.service_id in seen:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{service.title}' is on the invoice twice. Use one line.")
        if not service.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{service.title}' is deactivated; remove its line.")
        seen.add(item.service_id)
        lines.append(InvoiceLine(
            position=position,
            service_id=service.id,
            service_title=service.title,
            # Left empty, the service's catalogue description (older services have one) or its title is printed.
            description=item.description or service.description,
            sac_code=service.sac_code,
            gst_rate=service.gst_rate,
            taxable_value=item.taxable_value,
        ))
    return lines


def apply_financials(invoice: ClientInvoice) -> None:
    """Recompute tax and totals. With lines, the header is their sum and mirrors the first line's service."""
    if invoice.lines:
        _sum_lines(invoice)
    else:
        gst = compute_gst(invoice.taxable_value, invoice.gst_rate, invoice.is_interstate)
        invoice.gst_amount = gst.gst_amount
        invoice.cgst, invoice.sgst, invoice.igst = gst.cgst, gst.sgst, gst.igst
    invoice.total_amount = round(invoice.taxable_value + invoice.gst_amount, 2)
    invoice.expected_tds = compute_tds(invoice.taxable_value, invoice.tds_rate, invoice.tds_applicable)
    # amount_received is None until the row is flushed (column default not yet applied).
    if (invoice.amount_received or 0) <= 0:
        invoice.amount_pending = invoice.total_amount


def _sum_lines(invoice: ClientInvoice) -> None:
    for line in invoice.lines:
        gst = compute_gst(line.taxable_value, line.gst_rate, invoice.is_interstate)
        line.gst_amount, line.cgst, line.sgst, line.igst = gst.gst_amount, gst.cgst, gst.sgst, gst.igst

    def total(field: str) -> float:
        return round(sum(getattr(line, field) for line in invoice.lines), 2)

    invoice.taxable_value = total("taxable_value")
    invoice.gst_amount = total("gst_amount")
    invoice.cgst, invoice.sgst, invoice.igst = total("cgst"), total("sgst"), total("igst")
    first = invoice.lines[0]
    invoice.service_id = first.service_id
    invoice.service_title = first.service_title
    invoice.service_description = first.description
    invoice.sac_code = first.sac_code
    invoice.gst_rate = first.gst_rate


def lines_changed(invoice: ClientInvoice, inputs: list[LineInput]) -> bool:
    """True when ``inputs`` bill other services or amounts, or reword a line."""
    if len(inputs) != len(invoice.lines):
        return True
    for line, item in zip(invoice.lines, inputs):
        if (line.service_id, line.taxable_value) != (item.service_id, item.taxable_value):
            return True
        if item.description is not None and item.description != line.description:
            return True
    return False


def interstate_for(client: Client, requested: bool) -> bool:
    """IGST when the client's GSTIN is from another state; without a GSTIN the user's choice stands."""
    return is_interstate_supply(client.gst_number, settings.company_gstin, requested)
