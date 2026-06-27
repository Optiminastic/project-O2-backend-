from datetime import date

from sqlalchemy import String, Text, Float, Date, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import VerificationStatus


class BankStatement(Base, TimestampMixin):
    __tablename__ = "bank_statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    transaction_count: Mapped[int] = mapped_column(default=0)
    matched_count: Mapped[int] = mapped_column(default=0)

    transactions: Mapped[list["BankTransaction"]] = relationship(
        back_populates="statement", cascade="all, delete-orphan"
    )


class BankTransaction(Base, TimestampMixin):
    __tablename__ = "bank_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("bank_statements.id"))

    txn_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    utr_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty: Mapped[str | None] = mapped_column(String(200), nullable=True)

    verification_status: Mapped[VerificationStatus] = mapped_column(
        SAEnum(VerificationStatus), default=VerificationStatus.STATEMENT_UPLOADED
    )
    # Soft links to whatever record this transaction matched.
    matched_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    matched_approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_approvals.id"), nullable=True
    )
    match_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    statement: Mapped["BankStatement"] = relationship(back_populates="transactions")
