from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict


class ClientBase(BaseModel):
    business_name: str
    legal_name: str | None = None
    email: EmailStr
    phone: str | None = None
    billing_address: str | None = None
    gst_number: str | None = None
    coi: str | None = None
    category: str | None = None
    notes: str | None = None


class ClientCreate(ClientBase):
    pass


class ClientUpdate(BaseModel):
    business_name: str | None = None
    legal_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    billing_address: str | None = None
    gst_number: str | None = None
    coi: str | None = None
    category: str | None = None
    notes: str | None = None


class ClientOut(ClientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
