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
    # Udyam registration number. Present only for MSME-registered vendors.
    msme_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    coi: Mapped[str | None] = mapped_column(String(120), nullable=True)  # Certificate of Incorporation

    # Single-account bank details from before vendors could hold several
    # accounts. Copied into vendor_bank_accounts; no longer written.
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

    bank_accounts: Mapped[list["VendorBankAccount"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan", order_by="VendorBankAccount.id"
    )
    allocations: Mapped[list["VendorAllocation"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )
    invoices: Mapped[list["VendorInvoice"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )


class VendorBankAccount(Base, TimestampMixin):
    """A bank account a vendor can be paid into. A vendor may have several."""

    __tablename__ = "vendor_bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    account_holder: Mapped[str] = mapped_column(String(160))
    bank_name: Mapped[str] = mapped_column(String(160))
    account_number: Mapped[str] = mapped_column(String(40))
    ifsc_code: Mapped[str] = mapped_column(String(20))

    vendor: Mapped["Vendor"] = relationship(back_populates="bank_accounts")


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
    # The project this invoice bills for. allocation_id is kept only for invoices
    # recorded before projects existed.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)

    invoice_number: Mapped[str] = mapped_column(String(60), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    invoice_amount: Mapped[float] = mapped_column(Float, default=0.0)  # before GST
    gst_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # Null rate and split only on bills recorded before GST was split per bill.
    gst_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_interstate: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cgst: Mapped[float | None] = mapped_column(Float, nullable=True)
    sgst: Mapped[float | None] = mapped_column(Float, nullable=True)
    igst: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The vendor's GSTIN when the bill was recorded. Without it the GST is not claimable as input credit.
    vendor_gstin: Mapped[str | None] = mapped_column(String(30), nullable=True)

    tds_applicable: Mapped[bool] = mapped_column(Boolean, default=True)
    tds_section: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tds_rate: Mapped[float] = mapped_column(Float, default=2.0)
    tds_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # Deposit of the TDS above with the government (challan).
    tds_deposited_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    tds_challan_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    net_payable: Mapped[float] = mapped_column(Float, default=0.0)

    report_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[InvoiceStatus] = mapped_column(SAEnum(InvoiceStatus), default=InvoiceStatus.PENDING)

    vendor: Mapped["Vendor"] = relationship(back_populates="invoices")
