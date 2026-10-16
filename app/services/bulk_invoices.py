"""Bulk invoice upload: read a spreadsheet, check every row, and describe the proformas it would create.

The upload is all or nothing. ``check_rows`` never writes; the router creates
invoices only when every row is clean, inside one transaction, so a bad file
neither leaves half its rows behind nor consumes proforma numbers.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Client, Project, Service
from app.services import spreadsheet
from app.services.contract import billed_elsewhere, over_contract_message
from app.services.numbering import business_date
from app.services.spreadsheet import MAX_FILE_BYTES, MAX_ROWS, TableSpec, has_data, text_of
from app.services.taxation import (
    AMOUNT_DECIMALS,
    DEFAULT_CLIENT_TDS_RATE,
    MAX_AMOUNT,
    MAX_TDS_RATE,
    compute_gst,
    is_interstate_supply,
)

FIRST_DATA_ROW = 2  # row 1 holds the headers, as a spreadsheet user sees it

PROJECT, AMOUNT, SERVICE = "Project", "Amount", "Service"
DESCRIPTION, DUE_DATE, TDS, INTERSTATE, REMARKS = "Description", "Due date", "TDS %", "Interstate", "Remarks"
REQUIRED_COLUMNS = (PROJECT, AMOUNT)
COLUMNS = REQUIRED_COLUMNS + (SERVICE, DESCRIPTION, DUE_DATE, TDS, INTERSTATE, REMARKS)

YES = {"yes", "y", "true", "1"}
NO = {"no", "n", "false", "0", ""}
# Indian paperwork writes dates day first; ISO is accepted too.
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")


@dataclass
class RowCheck:
    row: int
    project_input: str
    project: Project | None = None
    # The project's service this row bills.
    service: Service | None = None
    amount: float | None = None
    description: str | None = None
    due_date: date | None = None
    tds_rate: float = DEFAULT_CLIENT_TDS_RATE
    is_interstate: bool = False
    remarks: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # The project decides who is billed.
    @property
    def client(self) -> Client | None:
        return self.project.client if self.project else None

    @property
    def gst(self):
        if self.amount is None or self.service is None:
            return None
        return compute_gst(self.amount, self.service.gst_rate, self.is_interstate)


# ---------- reading the file ----------

SPEC = TableSpec(required=REQUIRED_COLUMNS, columns=COLUMNS, sheet_name="Invoices", row_noun="invoice rows")


def read_rows(filename: str, content: bytes) -> list[dict[str, object]]:
    """Rows keyed by column name. Raises 400 if the file itself is unusable."""
    return spreadsheet.read_rows(SPEC, filename, content)


# ---------- checking each row ----------

def check_rows(db: Session, records: list[dict[str, object]]) -> list[RowCheck]:
    projects = {
        _key(p.code): p
        for p in db.query(Project).options(selectinload(Project.client), selectinload(Project.services)).all()
    }
    today = business_date()

    checks = []
    for offset, record in enumerate(records):
        if not has_data(record):
            continue
        check = RowCheck(
            row=FIRST_DATA_ROW + offset,
            project_input=text_of(record.get(PROJECT)),
            description=text_of(record.get(DESCRIPTION)) or None,
            remarks=text_of(record.get(REMARKS)) or None,
        )
        check.project = _find_project(projects, check.project_input, check.errors)
        if check.project:
            check.service = _find_service(check.project, text_of(record.get(SERVICE)), check.errors)
        check.amount = _amount(record.get(AMOUNT), check.errors)
        check.due_date = _due_date(record.get(DUE_DATE), today, check.errors)
        check.tds_rate = _tds_rate(record.get(TDS), check.errors)
        # A client with a GSTIN is billed by its state; the column counts only without one.
        requested = _yes_no(record.get(INTERSTATE), check.errors)
        gstin = check.client.gst_number if check.client else None
        check.is_interstate = is_interstate_supply(gstin, settings.company_gstin, requested)
        checks.append(check)

    _flag_repeats(checks)
    _flag_over_contract(db, checks)
    return checks


def _flag_over_contract(db: Session, checks: list[RowCheck]) -> None:
    """Rows are billed in file order, so each row counts the rows above it on the same project."""
    running: dict[int, float] = {}
    for c in checks:
        if c.project is None or c.amount is None:
            continue
        pid = c.project.id
        if pid not in running:
            running[pid] = billed_elsewhere(db, pid)
        message = over_contract_message(c.project, running[pid], c.amount)
        if message:
            c.errors.append(message)
        else:
            running[pid] = round(running[pid] + c.amount, 2)


def _find_project(projects: dict[str, Project], value: str, errors: list[str]) -> Project | None:
    if not value:
        errors.append("Project is required (its code).")
        return None
    project = projects.get(_key(value))
    if project is None:
        errors.append(f"No project with code '{value}'. Check the code or create the project first.")
        return None
    if project.client is None:
        errors.append(f"Project {project.code} has no client. Add one before invoicing.")
        return None
    if not project.services:
        errors.append(f"Project {project.code} has no service. Choose one before invoicing.")
        return None
    return project


def _find_service(project: Project, value: str, errors: list[str]) -> Service | None:
    """The row's service: named in the Service column, or the project's only service."""
    if not value:
        if len(project.services) > 1:
            names = ", ".join(s.title for s in project.services)
            errors.append(f"Project {project.code} bills several services ({names}). Name one in the Service column.")
            return None
        service = project.services[0]
    else:
        service = next((s for s in project.services if _key(s.title) == _key(value)), None)
        if service is None:
            errors.append(f"'{value}' is not a service of project {project.code}.")
            return None
    if not service.is_active:
        errors.append(f"'{service.title}' is deactivated.")
        return None
    return service


def _amount(value: object, errors: list[str]) -> float | None:
    number = _number(value)
    if number is None:
        errors.append("Amount must be a number (value before GST).")
        return None
    if number <= 0:
        errors.append("Amount must be more than zero.")
        return None
    if number > MAX_AMOUNT:
        errors.append(f"Amount is more than the ₹{MAX_AMOUNT:,.0f} limit for one invoice. Check for a typo.")
        return None
    if round(number, AMOUNT_DECIMALS) != number:
        errors.append("Amount can have at most 2 decimal places (paise).")
        return None
    return number


def _due_date(value: object, today: date, errors: list[str]) -> date | None:
    if value is None or text_of(value) == "":
        return None  # defaults to the end of the month when the proforma is created
    due = _date(value)
    if due is None:
        errors.append("Due date must be a date like 31/10/2026.")
        return None
    if due < today:
        errors.append("Due date is in the past.")
        return None
    return due


def _tds_rate(value: object, errors: list[str]) -> float:
    if value is None or text_of(value) == "":
        return DEFAULT_CLIENT_TDS_RATE
    rate = _number(value)
    if rate is None or not 0 <= rate <= MAX_TDS_RATE:
        errors.append(f"TDS % must be a number from 0 to {MAX_TDS_RATE:g}.")
        return DEFAULT_CLIENT_TDS_RATE
    return rate


def _yes_no(value: object, errors: list[str]) -> bool:
    if isinstance(value, bool):
        return value
    word = text_of(value).lower()
    if word in YES:
        return True
    if word not in NO:
        errors.append("Interstate must be Yes or No.")
    return False


def _flag_repeats(checks: list[RowCheck]) -> None:
    """The same project and amount twice is usually a copy-paste slip, but can be genuine."""
    seen: dict[tuple, int] = {}
    for c in checks:
        if c.project is None or c.amount is None:
            continue
        key = (c.project.id, c.amount)
        if key in seen:
            c.warnings.append(f"Same project and amount as row {seen[key]}. Check it is not a duplicate.")
        else:
            seen[key] = c.row


# ---------- value parsing ----------

def _key(value: str) -> str:
    return " ".join(value.lower().split())


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[,\s₹]|^rs\.?", "", text_of(value), flags=re.IGNORECASE)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = text_of(value)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ---------- the template ----------

def build_template(db: Session) -> bytes:
    """An Excel file with the header row, a dropdown of invoiceable projects, and instructions."""
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Invoices"
    sheet.append(list(COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for letter, width in zip("ABCDEFGH", (24, 14, 28, 36, 14, 8, 11, 30)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"

    projects = (
        db.query(Project)
        .options(selectinload(Project.client), selectinload(Project.services))
        .filter(Project.client_id.is_not(None), Project.services.any())
        .order_by(Project.code)
        .all()
    )
    lists = wb.create_sheet("Lists")
    lists.append(["Project code", "Title", "Client", "Services"])
    for p in projects:
        lists.append([p.code, p.title, p.client.business_name, ", ".join(s.title for s in p.services)])

    last_row = MAX_ROWS + 1
    if projects:
        dv = DataValidation(type="list", formula1=f"=Lists!$A$2:$A${len(projects) + 1}", allow_blank=True)
        sheet.add_data_validation(dv)
        dv.add(f"A2:A{last_row}")
    yes_no = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    sheet.add_data_validation(yes_no)
    yes_no.add(f"G2:G{last_row}")

    help_sheet = wb.create_sheet("How to fill")
    for line in _INSTRUCTIONS:
        help_sheet.append([line])
    help_sheet.column_dimensions["A"].width = 110

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_INSTRUCTIONS = (
    "One row = one proforma invoice. Rows can be for one project or many.",
    "",
    "Project (required): the project's code (see the Lists sheet). Its client is billed.",
    "Amount (required): the value before GST, in rupees. GST is added automatically.",
    "Service (needed when the project bills several services): which of its services this row bills.",
    "Description (optional): leave empty to print the service's title.",
    "Due date (optional): day first, e.g. 31/10/2026. Empty means the end of this month.",
    f"TDS % (optional): the TDS the client will deduct. Empty means {DEFAULT_CLIENT_TDS_RATE:g}%.",
    "Interstate (optional): only for clients without a GSTIN. With a GSTIN, its state decides (IGST outside Maharashtra).",
    "Remarks (optional): internal note, not shown to the client.",
    "",
    f"At most {MAX_ROWS} rows per file. Nothing is created until every row is correct.",
    "Every row becomes a draft proforma and follows the normal approval flow.",
)
