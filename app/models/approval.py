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
    tax_deductions: Mapped[float] = mapped_column(Float, default=0.0)
    net_payable: Mapped[float] = mapped_column(Float, default=0.0)
    bank_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_documents: Mapped[str | None] = mapped_column(String(300), nullable=True)

    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(SAEnum(ApprovalStatus), default=ApprovalStatus.DRAFT)

    cfo_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ceo_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actions: Mapped[list["ApprovalAction"]] = relationship(
        back_populates="approval", cascade="all, delete-orphan", order_by="ApprovalAction.id"
    )


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
