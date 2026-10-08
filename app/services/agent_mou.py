"""Referral agent MOU (Memorandum of Understanding) as a PDF.

A plain-language template of the commission terms the platform already
enforces. It is a starting point for the parties, not legal advice; the footer
says so.
"""

import io
from datetime import date
from xml.sax.saxutils import escape

from fastapi import HTTPException, status
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import settings
from app.models import Agent
from app.services.numbering import business_date

# Section 194H: TDS on commission paid to the agent.
AGENT_TDS_RATE = 2.0
NOTICE_DAYS = 30
MARGIN = 20 * mm


def require_company_name(document: str = "An MOU") -> str:
    """Our legal name for a generated document; 400 until it is configured."""
    name = settings.company_legal_name.strip()
    if not name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{document} needs your company's details. Set COMPANY_LEGAL_NAME in the backend settings first.",
        )
    return name


def _party(name: str, rows: list[tuple[str, str | None]]) -> str:
    details = "; ".join(f"{label} {value}" for label, value in rows if value)
    return f"{name}" + (f" ({details})" if details else "")


def mou_sections(agent: Agent, on: date | None = None) -> list[tuple[str, str]]:
    """(heading, text) pairs in order. Headings are empty for the preamble."""
    company = require_company_name()
    agent_name = agent.legal_name or agent.business_name
    rate = f"{agent.commission_rate:g}%"
    bank = ", ".join(
        part for part in (agent.bank_account_holder, agent.bank_name, agent.account_number, agent.ifsc_code and f"IFSC {agent.ifsc_code}") if part
    )
    return [
        ("", f"This Memorandum of Understanding is made on {(on or business_date()).strftime('%d %B %Y')} between:"),
        ("", _party(company, [("address", settings.company_address), ("GSTIN", settings.company_gstin), ("PAN", settings.company_pan)])
         + ', hereinafter "the Company"; and'),
        ("", _party(
            agent_name,
            [("address", agent.address), ("email", agent.email), ("phone", agent.phone), ("GSTIN", agent.gst_number), ("PAN", agent.pan)],
        )
         + ', hereinafter "the Agent".'),
        ("1. Purpose", "The Agent will introduce prospective clients to the Company. The Company decides independently whether to take on any client introduced."),
        ("2. Commission", f"For each client introduced by the Agent and recorded against the Agent, the Company will pay a commission of {rate} of the taxable value (excluding GST) of every tax invoice it issues to that client and credits to the Agent."),
        ("3. When commission is earned and paid", "Commission is earned only on issued tax invoices, not on proforma invoices or quotations, and only on amounts the client has actually paid. It is paid by the end of the month following the month in which the client's payment is received."),
        ("4. Taxes", f"The Company will deduct tax at source under section 194H of the Income-tax Act at the applicable rate, currently {AGENT_TDS_RATE:g}%, and issue the TDS certificate. If the Agent is registered for GST, the Agent will raise a valid GST invoice for the commission before payment."),
        ("5. Payment details", f"Commission will be paid by bank transfer to: {bank}." if bank else "Commission will be paid by bank transfer to the account the Agent confirms in writing."),
        ("6. Conduct", "The Agent has no authority to make commitments or collect money on behalf of the Company, and will keep confidential any information about the Company and its clients."),
        ("7. Term", f"This MOU continues until either party ends it with {NOTICE_DAYS} days' written notice. Commission already earned under clause 3 remains payable after it ends."),
        ("8. Law", "This MOU is governed by the laws of India."),
    ]


def mou_paragraphs(agent: Agent) -> list[str]:
    return [f"{heading} {text}".strip() for heading, text in mou_sections(agent)]


def render_mou_pdf(agent: Agent) -> bytes:
    if agent.is_house:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{agent.business_name} is in-house; there is no agent MOU to sign")

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13, spaceAfter=4)
    heading = ParagraphStyle("heading", parent=body, fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=1)
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=16, spaceAfter=2)
    subtitle = ParagraphStyle("subtitle", parent=body, alignment=1, textColor="#555555", spaceAfter=12)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor="#777777")

    story = [
        Paragraph("Memorandum of Understanding", title),
        Paragraph(f"Referral agent: {escape(agent.business_name)} · Ref. MOU/AGT/{agent.id:04d}", subtitle),
    ]
    for head, text in mou_sections(agent):
        if head:
            story.append(Paragraph(escape(head), heading))
        story.append(Paragraph(escape(text), body))

    story += [Spacer(1, 14 * mm), _signature_table(agent, body), Spacer(1, 8 * mm)]
    story.append(Paragraph("Generated by Project O2 from the agent's record. Review with your legal advisor before signing.", small))

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f"MOU - {agent.business_name}",
        author=settings.company_legal_name,
    ).build(story)
    return buffer.getvalue()


def _signature_table(agent: Agent, style: ParagraphStyle) -> Table:
    def block(party: str) -> list[Paragraph]:
        return [Paragraph("_" * 34, style), Paragraph(escape(party), style), Paragraph("Name, designation and date", style)]

    table = Table([[block(f"For {require_company_name()}"), block(f"For {agent.legal_name or agent.business_name}")]], colWidths="*")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table
