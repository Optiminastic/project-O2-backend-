from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Enum as SAEnum, Float, ForeignKey, Index, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import AllocationStatus
from app.models.mixins import TimestampMixin


class Project(Base, TimestampMixin):
    """A piece of client work, delivered by one or more vendors within a budget."""

    __tablename__ = "projects"
    # The code is how people refer to a project, so it is unique whatever the case.
    __table_args__ = (Index("uq_projects_code_lower", text("lower(code)"), unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))  # the internal "HOC code"
    title: Mapped[str] = mapped_column(String(200), index=True)
    brand: Mapped[str | None] = mapped_column(String(160), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Required for new projects; null only on projects carried over from old vendor allocations.
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    # The most the company plans to spend on vendors for this project.
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[AllocationStatus] = mapped_column(SAEnum(AllocationStatus), default=AllocationStatus.NOT_STARTED)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    client: Mapped["Client | None"] = relationship()  # noqa: F821
    vendors: Mapped[list["ProjectVendor"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectVendor.id"
    )


class ProjectVendor(Base, TimestampMixin):
    """A vendor's share of a project, and the work order that commits it."""

    __tablename__ = "project_vendors"
    __table_args__ = (UniqueConstraint("project_id", "vendor_id", name="uq_project_vendors_project_vendor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    work_percent: Mapped[float] = mapped_column(Float, default=0.0)
    agreed_cost: Mapped[float] = mapped_column(Float, default=0.0)  # before GST

    # Numbered (WO/<FY>/NNNN) the first time the work order is produced; never changes after.
    work_order_number: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    work_order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    work_order_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Share or cost changed after the work order was issued: it should be sent again.
    work_order_outdated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    project: Mapped["Project"] = relationship(back_populates="vendors")
    vendor: Mapped["Vendor"] = relationship()  # noqa: F821
