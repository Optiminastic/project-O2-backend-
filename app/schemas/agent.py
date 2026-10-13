from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict, model_validator

from app.schemas.fields import (
    Name,
    OptionalAccountNumber,
    OptionalGstin,
    OptionalIfsc,
    OptionalName,
    OptionalPan,
    OptionalPhone,
    OptionalText,
    Percent,
    check_pan_matches_gstin,
)

BANK_FIELDS = ("bank_account_holder", "account_number", "ifsc_code")


class AgentCreate(BaseModel):
    business_name: Name
    legal_name: OptionalName = None
    contact_person: OptionalName = None
    email: EmailStr
    phone: OptionalPhone = None
    address: OptionalText = None
    gst_number: OptionalGstin = None
    pan: OptionalPan = None
    bank_account_holder: OptionalName = None
    bank_name: OptionalName = None
    account_number: OptionalAccountNumber = None
    ifsc_code: OptionalIfsc = None
    commission_rate: Percent = 0.0
    notes: OptionalText = None

    @model_validator(mode="after")
    def _consistent(self):
        check_pan_matches_gstin(self.pan, self.gst_number)
        given = [f for f in BANK_FIELDS if getattr(self, f)]
        if given and len(given) != len(BANK_FIELDS):
            raise ValueError("Bank details need the account holder, account number and IFSC together.")
        return self


class AgentUpdate(BaseModel):
    business_name: Name | None = None
    legal_name: OptionalName = None
    contact_person: OptionalName = None
    email: EmailStr | None = None
    phone: OptionalPhone = None
    address: OptionalText = None
    gst_number: OptionalGstin = None
    pan: OptionalPan = None
    bank_account_holder: OptionalName = None
    bank_name: OptionalName = None
    account_number: OptionalAccountNumber = None
    ifsc_code: OptionalIfsc = None
    commission_rate: Percent | None = None
    is_active: bool | None = None
    notes: OptionalText = None

    @model_validator(mode="after")
    def _consistent(self):
        check_pan_matches_gstin(self.pan, self.gst_number)
        return self


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    business_name: str
    legal_name: str | None = None
    contact_person: str | None = None
    email: EmailStr
    phone: str | None = None
    address: str | None = None
    gst_number: str | None = None
    pan: str | None = None
    bank_account_holder: str | None = None
    bank_name: str | None = None
    account_number: str | None = None
    ifsc_code: str | None = None
    commission_rate: float = 0.0
    notes: str | None = None
    is_active: bool
    is_house: bool
    created_at: datetime


class AgentOption(BaseModel):
    """Names-only view for picking an agent; no commission or bank details."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    business_name: str
    is_house: bool
