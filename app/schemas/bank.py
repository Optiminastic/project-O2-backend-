from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.fields import Name

# Exactly the last four digits of the account number.
Last4 = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{4}$")]


class BankCreate(BaseModel):
    name: Name
    account_last4: Last4


class BankUpdate(BaseModel):
    name: Name | None = None
    account_last4: Last4 | None = None
    is_active: bool | None = None


class BankOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    account_last4: str
    label: str
    is_active: bool
    created_at: datetime
