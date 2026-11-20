from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CashEntryCreate(BaseModel):
    direction: Literal["IN", "OUT"]
    entry_date: date
    amount: float = Field(gt=0, le=1_000_000_000)
    # UPS codes (project vendor service ids); the amount is divided equally over them.
    assignment_ids: list[int] = Field(min_length=1, max_length=100)
    # Required for cash outward: the agent the cash is paid to.
    agent_id: int | None = None
    note: str | None = Field(None, max_length=2000)

    @field_validator("assignment_ids")
    @classmethod
    def _unique(cls, v: list[int]) -> list[int]:
        if len(set(v)) != len(v):
            raise ValueError("Each UPS code can be chosen once")
        return v


class CashLineOut(BaseModel):
    assignment_id: int
    code: str
    amount: float


class CashEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    direction: str
    entry_date: date
    amount: float
    agent_id: int | None
    agent_name: str | None
    note: str | None
    lines: list[CashLineOut]
    created_at: datetime


class UpsCodeOut(BaseModel):
    id: int
    code: str
    project_id: int
    project_code: str
    project_title: str
    service_title: str
    vendor_name: str


class CashAgentOut(BaseModel):
    id: int
    business_name: str


class CashMonthOut(BaseModel):
    month: str  # YYYY-MM
    invoice_taxable: float
    invoice_gst: float
    invoice_total: float
    invoice_count: int
    cash_received: float
    cash_paid: float
