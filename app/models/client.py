from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin


class Client(Base, TimestampMixin):
    """A client (or vendor-facing business entity) onboarded via the intake form."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_name: Mapped[str] = mapped_column(String(200), index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    billing_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    gst_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    coi: Mapped[str | None] = mapped_column(String(120), nullable=True)  # Certificate of Incorporation
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    invoices: Mapped[list["ClientInvoice"]] = relationship(  # noqa: F821
        back_populates="client", cascade="all, delete-orphan"
    )
