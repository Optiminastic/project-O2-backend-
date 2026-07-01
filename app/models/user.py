from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin
from app.models.enums import UserRole


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.FINANCE_EXECUTIVE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set on every successful login / signup / invite-accept so the CEO can see
    # whether an invited member has actually signed in yet (and when they were last active).
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
