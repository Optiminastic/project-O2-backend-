from datetime import date, datetime

from sqlalchemy import String, Text, Float, Boolean, Date, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import InvoiceStatus, PaymentMode, GstStatus


class ClientInvoice(Base, TimestampMixin):
    __tablename__ = "client_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))

    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_description: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    client: Mapped["Client"] = relationship(back_populates="invoices")  # noqa: F821
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class Payment(Base, TimestampMixin):
    """A payment received against a client invoice (partial or full)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("client_invoices.id"))

    amount: Mapped[float] = mapped_column(Float)
    payment_date: Mapped[date] = mapped_column(Date)
    bank_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_mode: Mapped[PaymentMode] = mapped_column(SAEnum(PaymentMode), default=PaymentMode.NEFT)
    tds_deducted: Mapped[float] = mapped_column(Float, default=0.0)
    gst_component: Mapped[float] = mapped_column(Float, default=0.0)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment: Mapped[str | None] = mapped_column(String(300), nullable=True)

    invoice: Mapped["ClientInvoice"] = relationship(back_populates="payments")
