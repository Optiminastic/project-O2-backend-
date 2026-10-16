from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict, model_validator

from app.schemas.fields import Name, OptionalCin, OptionalGstin, OptionalName, OptionalPhone, OptionalText, OptionalUdyam


class ClientCreate(BaseModel):
    business_name: Name
    legal_name: OptionalName = None
    email: EmailStr
    phone: OptionalPhone = None
    billing_address: OptionalText = None
    gst_number: OptionalGstin = None
    coi: OptionalCin = None
    msme: bool = False
    # The Udyam registration number, kept only for an MSME client.
    msme_number: OptionalUdyam = None
    category: OptionalName = None
    notes: OptionalText = None
    agent_id: int | None = None

    @model_validator(mode="after")
    def _msme_number_only_for_msme(self):
        if not self.msme:
            self.msme_number = None
        return self


class ClientUpdate(BaseModel):
    business_name: Name | None = None
    legal_name: OptionalName = None
    email: EmailStr | None = None
    phone: OptionalPhone = None
    billing_address: OptionalText = None
    gst_number: OptionalGstin = None
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
