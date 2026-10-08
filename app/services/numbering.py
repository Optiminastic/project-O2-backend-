"""Gapless document numbering per Indian financial year.

GST Rule 46 requires tax invoice numbers to be consecutive and unique within a
financial year, at most 16 characters. Numbers are drawn from
``document_sequences`` inside the caller's transaction: if that transaction
rolls back, the increment rolls back too, so no number is ever skipped.
"""

import calendar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

BUSINESS_TZ = ZoneInfo("Asia/Kolkata")
FY_START_MONTH = 4  # April
SEQUENCE_WIDTH = 4

PROFORMA_PREFIX = "PI"
TAX_INVOICE_PREFIX = "INV"


def business_date(at: datetime | None = None) -> date:
    """The calendar date in India. Invoice dates and FY boundaries are Indian dates, not UTC."""
    moment = at or datetime.now(timezone.utc)
    return moment.astimezone(BUSINESS_TZ).date()


def month_end(on: date) -> date:
    """Last day of ``on``'s month: the default invoice due date."""
    return date(on.year, on.month, calendar.monthrange(on.year, on.month)[1])


def financial_year(on: date) -> str:
    """'2026-27' for any date from 1 April 2026 to 31 March 2027."""
    start = on.year if on.month >= FY_START_MONTH else on.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


_INCREMENT = text(
    """
    INSERT INTO document_sequences (series, last_value) VALUES (:series, 1)
    ON CONFLICT (series) DO UPDATE SET last_value = document_sequences.last_value + 1
    RETURNING last_value
    """
)


def next_number(db: Session, prefix: str, on: date) -> str:
    """Reserve the next number in ``<prefix>/<FY>``. The caller owns the commit."""
    series = f"{prefix}/{financial_year(on)}"
    value = db.execute(_INCREMENT, {"series": series}).scalar_one()
    return f"{series}/{value:0{SEQUENCE_WIDTH}d}"
