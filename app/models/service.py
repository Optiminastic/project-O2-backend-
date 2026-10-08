from sqlalchemy import Boolean, Float, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class Service(Base, TimestampMixin):
    """A billable service in the catalogue. Picking it on an invoice pre-fills its tax details.

    Invoices copy the title, description, SAC code and GST rate when the service
    is chosen, so later catalogue edits never rewrite an invoice already raised.
    """

    __tablename__ = "services"
    __table_args__ = (Index("uq_services_title_lower", text("lower(title)"), unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Services Accounting Code: the GST classification printed on the invoice.
    sac_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    gst_rate: Mapped[float] = mapped_column(Float, default=18.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
