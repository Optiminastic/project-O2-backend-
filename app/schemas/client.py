from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator, model_validator

from app.schemas.fields import (
    OptionalCin,
    Gstin,
    LongText,
    Name,
    OptionalName,
    OptionalPhone,
    OptionalText,
    OptionalUdyam,
)

MSME_NUMBER_REQUIRED = "Enter the Udyam registration number for an MSME client."
MAX_OTHER_EMAILS = 10


def clean_other_emails(emails: list[str] | None, primary: str | None) -> list[str]:
    """Each address once, lower-cased, without the primary email."""
    seen: list[str] = []
    for e in emails or []:
        e = e.strip().lower()
        if e and e != (primary or "").strip().lower() and e not in seen:
            seen.append(e)
    return seen


class ClientCreate(BaseModel):
    """The client intake form: every field on it is required.

    Business name, email, GSTIN, address and the MSME answer must be sent, and an MSME
    client also needs its Udyam number. The COI is optional. ``other_emails`` also
    receive the client's invoices. The agent falls back to the in-house
    "Opti" agent when not sent. Legal name, phone, category and notes are not on the
    form and stay optional.
    """

    business_name: Name
    legal_name: OptionalName = None
    email: EmailStr
    other_emails: list[EmailStr] = Field(default=[], max_length=MAX_OTHER_EMAILS)
    phone: OptionalPhone = None
    billing_address: LongText
    gst_number: Gstin
    coi: OptionalCin = None
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
        self.other_emails = clean_other_emails(self.other_emails, self.email)
        return self


class ClientUpdate(BaseModel):
    """An edit sends only what changes, but cannot blank a required field."""

    business_name: Name | None = None
    legal_name: OptionalName = None
    email: EmailStr | None = None
    # When sent, replaces the list.
    other_emails: list[EmailStr] | None = Field(default=None, max_length=MAX_OTHER_EMAILS)
    phone: OptionalPhone = None
    billing_address: LongText | None = None
    gst_number: Gstin | None = None
    coi: OptionalCin = None
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
    other_emails: list[str] = []
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

    @field_validator("other_emails", mode="before")
    @classmethod
    def _split_emails(cls, value):
        if isinstance(value, str):
            return [e for e in value.split(",") if e]
        return value or []
