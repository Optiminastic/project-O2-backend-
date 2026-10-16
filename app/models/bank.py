from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class CompanyBank(Base, TimestampMixin):
    """One of our own bank accounts that clients pay into.

    Only the last four digits of the account are kept: enough to tell accounts
    apart on a receipt, without storing the full number.
    """

    __tablename__ = "company_banks"
    __table_args__ = (Index("uq_company_banks_name_last4", text("lower(name)"), "account_last4", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    account_last4: Mapped[str] = mapped_column(String(4))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    @property
    def label(self) -> str:
        """How the account reads on a receipt: 'IndusInd Bank ··2018'."""
        return f"{self.name} ··{self.account_last4}"
