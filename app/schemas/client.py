from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict, model_validator

from app.schemas.fields import (
    Cin,
    Gstin,
    LongText,
    Name,
    OptionalName,
    OptionalPhone,
    OptionalText,
    OptionalUdyam,
)

MSME_NUMBER_REQUIRED = "Enter the Udyam registration number for an MSME client."


class ClientCreate(BaseModel):
    """The client intake form: every field on it is required.

    Business name, email, GSTIN, address, COI and the MSME answer must be sent, and an
    MSME client also needs its Udyam number. The agent falls back to the in-house
    "Opti" agent when not sent. Legal name, phone, category and notes are not on the
    form and stay optional.
    """

    business_name: Name
    legal_name: OptionalName = None
    email: EmailStr
    phone: OptionalPhone = None
    billing_address: LongText
    gst_number: Gstin
    coi: Cin
    msme: bool
    # The Udyam registration number, kept only for (and required of) an MSME client.
    msme_number: OptionalUdyam = None
    category: OptionalName = None
    notes: OptionalText = None
    agent_id: int | None = None

    @model_validator(mode="after")
    def _msme_number_only_for_msme(self):
        if not self.msme:
            self.msme_number = None
        elif not self.msme_number:
            raise ValueError(MSME_NUMBER_REQUIRED)
        return self


class ClientUpdate(BaseModel):
    """An edit sends only what changes, but cannot blank a required field."""

    business_name: Name | None = None
    legal_name: OptionalName = None
    email: EmailStr | None = None
    phone: OptionalPhone = None
    billing_address: LongText | None = None
    gst_number: Gstin | None = None
    coi: Cin | None = None
    msme: bool | None = None
    msme_number: OptionalUdyam = None
    category: OptionalName = None
    notes: OptionalText = None
    agent_id: int | None = None


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    business_name: str
    legal_name: str | None = None
    email: EmailStr
    phone: str | None = None
    billing_address: str | None = None
    gst_number: str | None = None
    coi: str | None = None
    msme: bool = False
    msme_number: str | None = None
    category: str | None = None
    notes: str | None = None
    agent_id: int | None = None
    created_at: datetime
