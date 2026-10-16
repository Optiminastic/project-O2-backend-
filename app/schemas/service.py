from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.fields import GstRate, OptionalSacCode, OptionalText

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ServiceCreate(BaseModel):
    title: Title
    description: OptionalText = None
    sac_code: OptionalSacCode = None
    gst_rate: GstRate = 18.0


class ServiceUpdate(BaseModel):
    title: Title | None = None
    description: OptionalText = None
    sac_code: OptionalSacCode = None
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


class ServiceBulkRowOut(BaseModel):
    """One spreadsheet row of a bulk service upload, checked but not yet created."""

    row: int
    title: str
    sac_code: str | None
    errors: list[str]
    warnings: list[str]


class ServiceBulkPreviewOut(BaseModel):
    rows: list[ServiceBulkRowOut]
    # True only when no row has an error; the upload is all or nothing.
    ready: bool
    service_count: int
    error_count: int


class ServiceBulkCreatedOut(BaseModel):
    created: int
    services: list[ServiceOut]
