"""Bulk service upload: read a spreadsheet of services and check every row before any is created.

All or nothing, like the bulk invoice upload. A SAC code shared with another
service is only a warning: GST lets several services carry the same SAC.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy.orm import Session

from app.models import Service
from app.services import identifiers as ids
from app.services import spreadsheet
from app.services.spreadsheet import MAX_ROWS, TableSpec, has_data, text_of

FIRST_DATA_ROW = 2  # row 1 holds the headers, as a spreadsheet user sees it
TITLE_MAX = 200

TITLE, SAC = "Title", "SAC code"
SPEC = TableSpec(required=(TITLE,), columns=(TITLE, SAC), sheet_name="Services", row_noun="service rows")


@dataclass
class ServiceRowCheck:
    row: int
    title: str
    sac_code: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_rows(filename: str, content: bytes) -> list[dict[str, object]]:
    return spreadsheet.read_rows(SPEC, filename, content)


def check_rows(db: Session, records: list[dict[str, object]]) -> list[ServiceRowCheck]:
    existing = db.query(Service).all()
    taken_titles = {s.title.lower(): s.title for s in existing}
    sac_users: dict[str, list[str]] = {}
    for s in existing:
        if s.sac_code:
            sac_users.setdefault(s.sac_code, []).append(s.title)

    checks: list[ServiceRowCheck] = []
    for offset, record in enumerate(records):
        if not has_data(record):
            continue
        check = ServiceRowCheck(row=FIRST_DATA_ROW + offset, title=" ".join(text_of(record.get(TITLE)).split()))
        _check_title(check, taken_titles)
        check.sac_code = _sac(record.get(SAC), check.errors)
        if check.sac_code and check.sac_code in sac_users:
            check.warnings.append(f"SAC {check.sac_code} is already used by {', '.join(sac_users[check.sac_code])}.")
        checks.append(check)

    _flag_repeats(checks)
    return checks


def _check_title(check: ServiceRowCheck, taken: dict[str, str]) -> None:
    if not check.title:
        check.errors.append("Title is required.")
    elif len(check.title) > TITLE_MAX:
        check.errors.append(f"Title is longer than {TITLE_MAX} characters.")
    elif check.title.lower() in taken:
        check.errors.append(f"A service called '{taken[check.title.lower()]}' already exists.")


def _sac(value: object, errors: list[str]) -> str | None:
    """Optional. Spaces are dropped, as on the form; Excel may hand a number back as 998311.0."""
    raw = text_of(value)
    if isinstance(value, float) and value.is_integer():
        raw = str(int(value))
    code = re.sub(r"\s", "", raw)
    if not code:
        return None
    if not re.fullmatch(ids.SAC_PATTERN, code):
        errors.append("SAC code must be 4 to 8 digits (e.g. 998311).")
        return None
    return code


def _flag_repeats(checks: list[ServiceRowCheck]) -> None:
    """A title twice in the file is an error; the same SAC twice is a warning."""
    titles: dict[str, int] = {}
    sacs: dict[str, int] = {}
    for c in checks:
        key = c.title.lower()
        if key and key in titles:
            c.errors.append(f"Same title as row {titles[key]}.")
        elif key:
            titles[key] = c.row
        if c.sac_code and c.sac_code in sacs:
            c.warnings.append(f"Same SAC as row {sacs[c.sac_code]}.")
        elif c.sac_code:
            sacs[c.sac_code] = c.row


def build_template() -> bytes:
    """An Excel file with the header row and a sheet explaining it."""
    wb = Workbook()
    sheet = wb.active
    sheet.title = SPEC.sheet_name
    sheet.append(list(SPEC.columns))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.column_dimensions["A"].width = 60
    sheet.column_dimensions["B"].width = 14
    sheet.freeze_panes = "A2"

    help_sheet = wb.create_sheet("How to fill")
    for line in _INSTRUCTIONS:
        help_sheet.append([line])
    help_sheet.column_dimensions["A"].width = 110

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_INSTRUCTIONS = (
    "One row = one service in the catalogue.",
    "",
    "Title (required): what you bill for, as it should print on the invoice. Titles must be unique.",
    "SAC code (optional): 4 to 8 digits, e.g. 998311. Several services may share a SAC; the preview points it out.",
    "",
    f"At most {MAX_ROWS} rows per file. Nothing is created until every row is correct.",
)
