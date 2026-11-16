from datetime import datetime

from sqlalchemy import String, Text, Float, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import ApprovalStatus


class PaymentApproval(Base, TimestampMixin):
    """An outgoing-payment approval request governed by CFO -> CEO sign-off."""

    __tablename__ = "payment_approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    payee_name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor_invoices.id"), nullable=True
    )
    # The vendor paid, for payouts raised against vendor bills.
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), nullable=True, index=True)
    tax_deductions: Mapped[float] = mapped_column(Float, default=0.0)
    net_payable: Mapped[float] = mapped_column(Float, default=0.0)
    bank_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_documents: Mapped[str | None] = mapped_column(String(300), nullable=True)

    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(SAEnum(ApprovalStatus), default=ApprovalStatus.DRAFT)

    cfo_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ceo_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Bank transaction reference (UTR / txn id) of the actual payout — mandatory to release.
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actions: Mapped[list["ApprovalAction"]] = relationship(
        back_populates="approval", cascade="all, delete-orphan", order_by="ApprovalAction.id"
    )
    # How a vendor payout is split over the bills (and their service lines) it settles.
    allocations: Mapped[list["PayoutAllocation"]] = relationship(
        back_populates="approval", cascade="all, delete-orphan", order_by="PayoutAllocation.id"
    )


class PayoutAllocation(Base):
    """The part of a vendor payout that settles one service line (UPS code) of one bill.

    ``line_id`` is empty only for bills recorded before per-service lines existed.
    """

    __tablename__ = "payout_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    approval_id: Mapped[int] = mapped_column(ForeignKey("payment_approvals.id", ondelete="CASCADE"), index=True)
    vendor_invoice_id: Mapped[int] = mapped_column(ForeignKey("vendor_invoices.id"), index=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("vendor_invoice_lines.id"), nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    approval: Mapped["PaymentApproval"] = relationship(back_populates="allocations")
    invoice: Mapped["VendorInvoice"] = relationship()  # noqa: F821
    line: Mapped["VendorInvoiceLine | None"] = relationship()  # noqa: F821

    @property
    def invoice_number(self) -> str:
        return self.invoice.invoice_number

    @property
    def code(self) -> str | None:
        return self.line.code if self.line else None


class ApprovalAction(Base, TimestampMixin):
    """Immutable audit entry for a single approval action."""

    __tablename__ = "approval_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    approval_id: Mapped[int] = mapped_column(ForeignKey("payment_approvals.id"))
    approver_name: Mapped[str] = mapped_column(String(120))
    approver_role: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(60))  # e.g. "CFO Approved"
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    changes: Mapped[str | None] = mapped_column(Text, nullable=True)

    approval: Mapped["PaymentApproval"] = relationship(back_populates="actions")
