from datetime import date, datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer, String, Table, Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import AllocationStatus
from app.models.mixins import TimestampMixin


# The services a project bills. Each one becomes a line on the project's invoices.
project_services = Table(
    "project_services",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("service_id", ForeignKey("services.id"), primary_key=True, index=True),
)


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
    # The first of ``services``, kept for projects made before a project could bill several.
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), nullable=True, index=True)
    # The fee agreed with the client, before GST. 0 means no billing limit is known.
    contract_value: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[AllocationStatus] = mapped_column(SAEnum(AllocationStatus), default=AllocationStatus.NOT_STARTED)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # The last "V01" handed to a vendor on this project; never reused, even after a vendor is removed.
    last_vendor_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    client: Mapped["Client | None"] = relationship()  # noqa: F821
    service: Mapped["Service | None"] = relationship()  # noqa: F821
    services: Mapped[list["Service"]] = relationship(  # noqa: F821
        secondary=project_services, order_by="Service.title"
    )
    vendors: Mapped[list["ProjectVendor"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectVendor.id"
    )


class ProjectVendor(Base, TimestampMixin):
    """A vendor on a project: the services they deliver, and the one work order that commits them."""

    __tablename__ = "project_vendors"
    __table_args__ = (UniqueConstraint("project_id", "vendor_id", name="uq_project_vendors_project_vendor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    # No longer set: a vendor's share follows from the cost of its services. Kept for old rows.
    work_percent: Mapped[float] = mapped_column(Float, default=0.0)
    # The sum of the services' agreed costs, before GST.
    agreed_cost: Mapped[float] = mapped_column(Float, default=0.0)
    # The "V01" in this vendor's service codes on the project; never reused.
    vendor_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The last "S01" handed out to this vendor's services here; never reused.
    last_service_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Numbered (WO/<FY>/NNNN) the first time the work order is produced; never changes after.
    work_order_number: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    work_order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    work_order_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Share or cost changed after the work order was issued: it should be sent again.
    work_order_outdated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    project: Mapped["Project"] = relationship(back_populates="vendors")
    vendor: Mapped["Vendor"] = relationship()  # noqa: F821
    services: Mapped[list["ProjectVendorService"]] = relationship(
        back_populates="project_vendor", cascade="all, delete-orphan", order_by="ProjectVendorService.id"
    )


class ProjectVendorService(Base, TimestampMixin):
    """One service a vendor delivers on a project, under its own permanent code.

    The code (e.g. PRJ-0042-V01-S02) goes on the work order, and every vendor
    bill must charge against one or more codes.
    """

    __tablename__ = "project_vendor_services"
    __table_args__ = (UniqueConstraint("project_vendor_id", "service_id", name="uq_project_vendor_services_service"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_vendor_id: Mapped[int] = mapped_column(ForeignKey("project_vendors.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    agreed_cost: Mapped[float] = mapped_column(Float, default=0.0)  # before GST

    project_vendor: Mapped["ProjectVendor"] = relationship(back_populates="services")
    service: Mapped["Service"] = relationship()  # noqa: F821
