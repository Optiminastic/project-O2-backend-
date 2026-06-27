from datetime import date

from sqlalchemy import String, Text, Date, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import ReportReviewStatus


class VendorReport(Base, TimestampMixin):
    __tablename__ = "vendor_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    allocation_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor_allocations.id"), nullable=True
    )

    project_name: Mapped[str] = mapped_column(String(200))
    reporting_period: Mapped[str | None] = mapped_column(String(80), nullable=True)
    report_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    uploaded_file: Mapped[str | None] = mapped_column(String(300), nullable=True)
    submission_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    internal_reviewer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    review_status: Mapped[ReportReviewStatus] = mapped_column(
        SAEnum(ReportReviewStatus), default=ReportReviewStatus.SUBMITTED
    )
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    email_logs: Mapped[list["EmailLog"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class EmailLog(Base, TimestampMixin):
    """A record of a report emailed to a client."""

    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("vendor_reports.id"))
    to_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(40), default="Queued")
    sent_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    report: Mapped["VendorReport"] = relationship(back_populates="email_logs")
