from datetime import date

from sqlalchemy import Date, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin

CASH_IN = "IN"  # cash received
CASH_OUT = "OUT"  # cash paid out to an agent


class CashEntry(Base, TimestampMixin):
    """Cash received, or cash paid out to an agent, against one or more UPS codes.

    The amount is divided equally over the UPS codes (in paise; see CashEntryLine).
    """

    __tablename__ = "cash_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    direction: Mapped[str] = mapped_column(String(3), index=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    # Who the cash went to, for cash outward.
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    agent: Mapped["Agent | None"] = relationship()  # noqa: F821
    lines: Mapped[list["CashEntryLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="CashEntryLine.id"
    )


class CashEntryLine(Base):
    """The share of a cash entry on one UPS code."""

    __tablename__ = "cash_entry_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("cash_entries.id", ondelete="CASCADE"), index=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("project_vendor_services.id"), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    entry: Mapped[CashEntry] = relationship(back_populates="lines")
    assignment: Mapped["ProjectVendorService"] = relationship()  # noqa: F821
