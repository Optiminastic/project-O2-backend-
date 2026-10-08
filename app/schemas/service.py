from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
SacCode = Annotated[str, StringConstraints(strip_whitespace=True, max_length=12)]
# Indian GST slabs run from 0% to 40%.
GstRate = Annotated[float, Field(ge=0, le=40)]


class ServiceCreate(BaseModel):
    title: Title
    description: str | None = None
    sac_code: SacCode | None = None
    gst_rate: GstRate = 18.0


class ServiceUpdate(BaseModel):
    title: Title | None = None
    description: str | None = None
    sac_code: SacCode | None = None
    gst_rate: GstRate | None = None
    is_active: bool | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    sac_code: str | None
    gst_rate: float
    is_active: bool
    created_at: datetime
