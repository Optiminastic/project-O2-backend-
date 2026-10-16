from datetime import date, datetime
from types import SimpleNamespace

from sqlalchemy import String, Text, Float, Boolean, Date, DateTime, ForeignKey, Integer, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import (
    PROFORMA_STAGES,
    ClientApprovalMethod,
    GstStatus,
    InvoiceLinkPurpose,
    InvoiceStatus,
    PaymentMode,
)


class ClientInvoice(Base, TimestampMixin):
    __tablename__ = "client_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tax invoice number. Assigned only when a manager issues the invoice, so a
    # rejected proforma never leaves a gap in the GST invoice series.
    invoice_number: Mapped[str | None] = mapped_column(String(60), unique=True, index=True, nullable=True)
    proforma_number: Mapped[str | None] = mapped_column(String(60), unique=True, index=True, nullable=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    # Agent credited with commission on this invoice. Mandatory; defaults to the
    # client's agent, and the in-house "Opti" earns nothing.
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))

    # Tax invoice date (the GST time of supply). Set at issue.
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    proforma_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The project billed. Client and service details are copied from it when chosen.
    # Nullable only for invoices raised before invoices belonged to projects.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    # The catalogue service billed. Title, description, SAC and GST rate are
    # copied onto the invoice when chosen, so catalogue edits never rewrite it.
    # Nullable only for invoices raised before the catalogue existed.
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    service_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    service_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Services Accounting Code - the GST classification of the service.
    sac_code: Mapped[str | None] = mapped_column(String(12), nullable=True)

    # Financials
    taxable_value: Mapped[float] = mapped_column(Float, default=0.0)
    gst_rate: Mapped[float] = mapped_column(Float, default=18.0)
    gst_amount: Mapped[float] = mapped_column(Float, default=0.0)
    cgst: Mapped[float] = mapped_column(Float, default=0.0)
    sgst: Mapped[float] = mapped_column(Float, default=0.0)
    igst: Mapped[float] = mapped_column(Float, default=0.0)
    is_interstate: Mapped[bool] = mapped_column(Boolean, default=False)
    tds_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    tds_rate: Mapped[float] = mapped_column(Float, default=0.0)
    expected_tds: Mapped[float] = mapped_column(Float, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    amount_received: Mapped[float] = mapped_column(Float, default=0.0)
    amount_pending: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[InvoiceStatus] = mapped_column(SAEnum(InvoiceStatus), default=InvoiceStatus.DRAFT)
    gst_status: Mapped[GstStatus] = mapped_column(SAEnum(GstStatus), default=GstStatus.PENDING_COLLECTION)

    # Locking — once any payment is recorded, critical fields become immutable.
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    supporting_document: Mapped[str | None] = mapped_column(String(300), nullable=True)
    internal_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Workflow: proforma -> client approval -> manager issue.
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    client_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_approval_method: Mapped[ClientApprovalMethod | None] = mapped_column(
        SAEnum(ClientApprovalMethod), nullable=True
    )
    client_approver_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    client_approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_request_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_requested_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    issued_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped["Client"] = relationship(back_populates="invoices")  # noqa: F821
    project: Mapped["Project | None"] = relationship()  # noqa: F821
    agent: Mapped["Agent"] = relationship(back_populates="invoices")  # noqa: F821
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    # One line per service billed. Empty only on invoices raised before lines existed,
    # whose single service lives in the header fields above.
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.position"
    )
    links: Mapped[list["InvoiceLink"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )

    @property
    def is_issued(self) -> bool:
        """True once this is a tax invoice (legacy rows included)."""
        return self.status not in PROFORMA_STAGES

    @property
    def line_items(self) -> list:
        """The invoice's lines; an invoice raised before lines existed reads as one line from its header."""
        if self.lines:
            return list(self.lines)
        return [SimpleNamespace(
            id=None,
            position=0,
            service_id=self.service_id,
            service_title=self.service_title or "Professional services",
            description=self.service_description,
            sac_code=self.sac_code,
            gst_rate=self.gst_rate,
            taxable_value=self.taxable_value,
            gst_amount=self.gst_amount,
            cgst=self.cgst,
            sgst=self.sgst,
            igst=self.igst,
        )]

    @property
    def display_number(self) -> str:
        if self.is_issued or not self.proforma_number:
            return self.invoice_number or self.proforma_number or f"#{self.id}"
        return self.proforma_number


class InvoiceLine(Base, TimestampMixin):
    """One service billed on an invoice, with its own SAC code and tax.

    Service details are copied when the line is made, so catalogue edits never rewrite it.
    """

    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("client_invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    service_title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sac_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    gst_rate: Mapped[float] = mapped_column(Float, default=18.0)
    taxable_value: Mapped[float] = mapped_column(Float, default=0.0)
    gst_amount: Mapped[float] = mapped_column(Float, default=0.0)
    cgst: Mapped[float] = mapped_column(Float, default=0.0)
    sgst: Mapped[float] = mapped_column(Float, default=0.0)
    igst: Mapped[float] = mapped_column(Float, default=0.0)

    invoice: Mapped["ClientInvoice"] = relationship(back_populates="lines")


class Payment(Base, TimestampMixin):
    """A payment received against a client invoice (partial or full)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("client_invoices.id"))

    amount: Mapped[float] = mapped_column(Float)
    payment_date: Mapped[date] = mapped_column(Date)
    bank_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_mode: Mapped[PaymentMode] = mapped_column(SAEnum(PaymentMode), default=PaymentMode.BANK)
    # The bank account the money was received into, as it read when recorded
    # ("IndusInd Bank ··2018"). Null only on older payments.
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # One of our banks (Banks page). Null on payments recorded before banks were listed.
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("company_banks.id"), nullable=True, index=True)
    tds_deducted: Mapped[float] = mapped_column(Float, default=0.0)
    # Form 16A from the client for the TDS above; it is what lets us claim the credit.
    tds_certificate_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tds_certificate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gst_component: Mapped[float] = mapped_column(Float, default=0.0)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Shared by the rows of one bank receipt that paid several invoices of the client.
    receipt_ref: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    invoice: Mapped["ClientInvoice"] = relationship(back_populates="payments")


class InvoiceLink(Base, TimestampMixin):
    """A secure, expiring link that lets a client open one invoice without logging in."""

    __tablename__ = "invoice_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("client_invoices.id"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    purpose: Mapped[InvoiceLinkPurpose] = mapped_column(SAEnum(InvoiceLinkPurpose))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    invoice: Mapped["ClientInvoice"] = relationship(back_populates="links")


class DocumentSequence(Base):
    """Last number handed out per document series, e.g. ``INV/2026-27``.

    Never cleared (not even by the CEO data erase): a number already sent to a
    client must never be issued again.
    """

    __tablename__ = "document_sequences"

    series: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)
