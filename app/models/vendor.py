from datetime import date

from sqlalchemy import String, Text, Float, Boolean, Date, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import VendorApprovalStatus, AllocationStatus, InvoiceStatus


class Vendor(Base, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_name: Mapped[str] = mapped_column(String(200), index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    gst_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Bank details
    bank_account_holder: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ifsc_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    tax_applicable: Mapped[bool] = mapped_column(Boolean, default=True)
    compliance_documents: Mapped[str | None] = mapped_column(String(300), nullable=True)
    annual_service_contract: Mapped[str | None] = mapped_column(String(300), nullable=True)

    approval_status: Mapped[VendorApprovalStatus] = mapped_column(
        SAEnum(VendorApprovalStatus), default=VendorApprovalStatus.PENDING
    )
    # Onboarding is complete only when all mandatory financial/tax/bank details are verified.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    allocations: Mapped[list["VendorAllocation"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )
    invoices: Mapped[list["VendorInvoice"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )


class VendorAllocation(Base, TimestampMixin):
    """Allocation of a vendor to a client project / campaign."""

    __tablename__ = "vendor_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)

    project_name: Mapped[str] = mapped_column(String(200))
    scope_of_work: Mapped[str | None] = mapped_column(Text, nullable=True)
    agreed_cost: Mapped[float] = mapped_column(Float, default=0.0)
    vendor_margin: Mapped[float] = mapped_column(Float, default=0.0)  # percent
    allocation_percent: Mapped[float] = mapped_column(Float, default=100.0)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    internal_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[AllocationStatus] = mapped_column(
        SAEnum(AllocationStatus), default=AllocationStatus.NOT_STARTED
    )

    vendor: Mapped["Vendor"] = relationship(back_populates="allocations")


class VendorInvoice(Base, TimestampMixin):
    __tablename__ = "vendor_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    allocation_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor_allocations.id"), nullable=True
    )

    invoice_number: Mapped[str] = mapped_column(String(60), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    invoice_amount: Mapped[float] = mapped_column(Float, default=0.0)
    gst_amount: Mapped[float] = mapped_column(Float, default=0.0)
    tds_applicable: Mapped[bool] = mapped_column(Boolean, default=True)
    tds_rate: Mapped[float] = mapped_column(Float, default=2.0)
    tds_amount: Mapped[float] = mapped_column(Float, default=0.0)
    net_payable: Mapped[float] = mapped_column(Float, default=0.0)

    report_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[InvoiceStatus] = mapped_column(SAEnum(InvoiceStatus), default=InvoiceStatus.PENDING)

    vendor: Mapped["Vendor"] = relationship(back_populates="invoices")
