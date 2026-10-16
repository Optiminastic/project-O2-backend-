from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import utcnow


class DataUpgrade(Base):
    """A one-time data step that has already run, such as adding the standard service list."""

    __tablename__ = "data_upgrades"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
