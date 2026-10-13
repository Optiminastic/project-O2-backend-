from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict

from app.schemas.fields import Name, OptionalCin, OptionalGstin, OptionalName, OptionalPhone, OptionalText


class ClientCreate(BaseModel):
    business_name: Name
    legal_name: OptionalName = None
    email: EmailStr
    phone: OptionalPhone = None
    billing_address: OptionalText = None
    gst_number: OptionalGstin = None
    coi: OptionalCin = None
    category: OptionalName = None
    notes: OptionalText = None
    agent_id: int | None = None


class ClientUpdate(BaseModel):
    business_name: Name | None = None
    legal_name: OptionalName = None
    email: EmailStr | None = None
    phone: OptionalPhone = None
    billing_address: OptionalText = None
    gst_number: OptionalGstin = None
    coi: OptionalCin = None
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
    category: str | None = None
    notes: str | None = None
    agent_id: int | None = None
    created_at: datetime
