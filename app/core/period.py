"""Month filter shared by list endpoints: ``?month=YYYY-MM`` in India time.

GST, TDS and most reporting run on Indian calendar months, so a timestamp is
placed in a month by its India-time date (00:30 IST on 1 October is October,
even though it is still 30 September in UTC).
"""

from dataclasses import dataclass
from datetime import date, datetime, time

from fastapi import Query
from sqlalchemy import and_
from sqlalchemy.sql.elements import ColumnElement

from app.services.numbering import BUSINESS_TZ

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


@dataclass(frozen=True)
class Period:
    start: date  # first day of the month
    end: date  # first day of the next month (exclusive)

    def dates(self, column) -> ColumnElement[bool]:
        """Filter for a DATE column."""
        return and_(column >= self.start, column < self.end)

    def timestamps(self, column) -> ColumnElement[bool]:
        """Filter for a TIMESTAMPTZ column, using India-time month boundaries."""
        start = datetime.combine(self.start, time.min, tzinfo=BUSINESS_TZ)
        end = datetime.combine(self.end, time.min, tzinfo=BUSINESS_TZ)
        return and_(column >= start, column < end)

    def contains(self, day: date | None) -> bool:
        return day is not None and self.start <= day < self.end


def month_period(
    month: str | None = Query(None, pattern=MONTH_PATTERN, description="Limit to one month, YYYY-MM (India time)"),
) -> Period | None:
    """FastAPI dependency: None means all time."""
    if month is None:
        return None
    year, mon = (int(part) for part in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return Period(start=start, end=end)
