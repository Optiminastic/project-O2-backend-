"""Bulk invoice upload: read a spreadsheet, check every row, and describe the proformas it would create.

The upload is all or nothing. ``check_rows`` never writes; the router creates
invoices only when every row is clean, inside one transaction, so a bad file
neither leaves half its rows behind nor consumes proforma numbers.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from fastapi import HTTPException, status
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.orm import Session

from app.models import Client, Service
from app.services.numbering import business_date
from app.services.taxation import DEFAULT_CLIENT_TDS_RATE, compute_gst

MAX_ROWS = 500
MAX_FILE_BYTES = 2 * 1024 * 1024
FIRST_DATA_ROW = 2  # row 1 holds the headers, as a spreadsheet user sees it

CLIENT, SERVICE, AMOUNT = "Client", "Service", "Amount"
DESCRIPTION, DUE_DATE, TDS, INTERSTATE, REMARKS = "Description", "Due date", "TDS %", "Interstate", "Remarks"
REQUIRED_COLUMNS = (CLIENT, SERVICE, AMOUNT)
COLUMNS = REQUIRED_COLUMNS + (DESCRIPTION, DUE_DATE, TDS, INTERSTATE, REMARKS)

GSTIN_LENGTH = 15
YES = {"yes", "y", "true", "1"}
NO = {"no", "n", "false", "0", ""}
# Indian paperwork writes dates day first; ISO is accepted too.
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")


@dataclass
class RowCheck:
    row: int
    client_input: str
    service_input: str
    client: Client | None = None
    service: Service | None = None
    amount: float | None = None
    description: str | None = None
    due_date: date | None = None
    tds_rate: float = DEFAULT_CLIENT_TDS_RATE
    is_interstate: bool = False
    remarks: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def gst(self):
        if self.amount is None or self.service is None:
            return None
        return compute_gst(self.amount, self.service.gst_rate, self.is_interstate)


# ---------- reading the file ----------

def read_rows(filename: str, content: bytes) -> list[dict[str, object]]:
    """Rows keyed by column name, blank rows dropped. Raises 400 if the file itself is unusable."""
    if len(content) > MAX_FILE_BYTES:
        raise _bad_file("The file is larger than 2 MB. Split it into smaller uploads.")
    name = filename.lower()
    if name.endswith(".xlsx"):
        table = _xlsx_table(content)
    elif name.endswith(".csv"):
        table = _csv_table(content)
    else:
        raise _bad_file("Upload the invoice template as an Excel (.xlsx) or CSV file.")
    if not table:
        raise _bad_file("The file is empty. Use the template's header row.")

    headers = [str(h or "").strip().lower() for h in table[0]]
    missing = [c for c in REQUIRED_COLUMNS if c.lower() not in headers]
    if missing:
        raise _bad_file(f"Missing column(s): {', '.join(missing)}. Use the template's header row.")
    index = {c: headers.index(c.lower()) for c in COLUMNS if c.lower() in headers}

    rows = []
    for values in table[1:]:
        record = {c: values[i] if i < len(values) else None for c, i in index.items()}
        rows.append(record)
    if not any(_has_data(r) for r in rows):
        raise _bad_file("The file has no invoice rows under the header.")
    if sum(_has_data(r) for r in rows) > MAX_ROWS:
        raise _bad_file(f"A single upload can hold at most {MAX_ROWS} invoices. Split the file.")
    return rows


def _xlsx_table(content: bytes) -> list[list[object]]:
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - any parse failure means the same thing to the user
        raise _bad_file("This Excel file could not be read. Save it as .xlsx and try again.")
    ws = wb["Invoices"] if "Invoices" in wb.sheetnames else wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _csv_table(content: bytes) -> list[list[object]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _bad_file("Save the CSV with UTF-8 encoding and try again.")
    return [r for r in csv.reader(io.StringIO(text))]


def _has_data(record: dict[str, object]) -> bool:
    return any(_text(v) for v in record.values())


def _bad_file(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


# ---------- checking each row ----------

def check_rows(db: Session, records: list[dict[str, object]]) -> list[RowCheck]:
    clients = _ClientLookup(db.query(Client).all())
    services = {s.title.strip().lower(): s for s in db.query(Service).all()}
    today = business_date()

    checks = []
    for offset, record in enumerate(records):
        if not _has_data(record):
            continue
        check = RowCheck(
            row=FIRST_DATA_ROW + offset,
            client_input=_text(record.get(CLIENT)),
            service_input=_text(record.get(SERVICE)),
            description=_text(record.get(DESCRIPTION)) or None,
            remarks=_text(record.get(REMARKS)) or None,
        )
        check.client = clients.find(check.client_input, check.errors)
        check.service = _find_service(services, check.service_input, check.errors)
        check.amount = _amount(record.get(AMOUNT), check.errors)
        check.due_date = _due_date(record.get(DUE_DATE), today, check.errors)
        check.tds_rate = _tds_rate(record.get(TDS), check.errors)
        check.is_interstate = _yes_no(record.get(INTERSTATE), check.errors)
        checks.append(check)

    _flag_repeats(checks)
    return checks


class _ClientLookup:
    def __init__(self, clients: list[Client]):
        self.by_gstin = {c.gst_number.strip().upper(): c for c in clients if c.gst_number}
        self.by_name: dict[str, list[Client]] = {}
        for c in clients:
            self.by_name.setdefault(_key(c.business_name), []).append(c)

    def find(self, value: str, errors: list[str]) -> Client | None:
        if not value:
            errors.append("Client is required (name or GSTIN).")
            return None
        if len(value) == GSTIN_LENGTH and value.upper() in self.by_gstin:
            return self.by_gstin[value.upper()]
        matches = self.by_name.get(_key(value), [])
        if len(matches) == 1:
            return matches[0]
        if matches:
            errors.append(f"{len(matches)} clients are called '{value}'; use their GSTIN instead.")
        else:
            errors.append(f"No client called '{value}'. Check the spelling or add the client first.")
        return None


def _find_service(services: dict[str, Service], value: str, errors: list[str]) -> Service | None:
    if not value:
        errors.append("Service is required.")
        return None
    service = services.get(_key(value))
    if service is None:
        errors.append(f"No service called '{value}'. Pick one from the Services page.")
        return None
    if not service.is_active:
        errors.append(f"'{service.title}' is deactivated; choose another service.")
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
    return round(number, 2)


def _due_date(value: object, today: date, errors: list[str]) -> date | None:
    if value is None or _text(value) == "":
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
    if value is None or _text(value) == "":
        return DEFAULT_CLIENT_TDS_RATE
    rate = _number(value)
    if rate is None or not 0 <= rate <= 100:
        errors.append("TDS % must be a number from 0 to 100.")
        return DEFAULT_CLIENT_TDS_RATE
    return rate


def _yes_no(value: object, errors: list[str]) -> bool:
    if isinstance(value, bool):
        return value
    word = _text(value).lower()
    if word in YES:
        return True
    if word not in NO:
        errors.append("Interstate must be Yes or No.")
    return False


def _flag_repeats(checks: list[RowCheck]) -> None:
    """The same client, service and amount twice is usually a copy-paste slip, but can be genuine."""
    seen: dict[tuple, int] = {}
    for c in checks:
        if c.client is None or c.service is None or c.amount is None:
            continue
        key = (c.client.id, c.service.id, c.amount)
        if key in seen:
            c.warnings.append(f"Same client, service and amount as row {seen[key]}. Check it is not a duplicate.")
        else:
            seen[key] = c.row


# ---------- value parsing ----------

def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _key(value: str) -> str:
    return " ".join(value.lower().split())


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[,\s₹]|^rs\.?", "", _text(value), flags=re.IGNORECASE)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ---------- the template ----------

def build_template(db: Session) -> bytes:
    """An Excel file with the header row, dropdowns of real clients and services, and instructions."""
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Invoices"
    sheet.append(list(COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for letter, width in zip("ABCDEFGH", (32, 28, 14, 36, 14, 8, 11, 30)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"

    clients = sorted(c.business_name for c in db.query(Client).all())
    services = sorted(s.title for s in db.query(Service).filter(Service.is_active.is_(True)).all())
    lists = wb.create_sheet("Lists")
    lists.append(["Clients", "Services"])
    for i in range(max(len(clients), len(services))):
        lists.append([clients[i] if i < len(clients) else None, services[i] if i < len(services) else None])

    last_row = MAX_ROWS + 1
    for column, source, count in (("A", "A", len(clients)), ("B", "B", len(services))):
        if count:
            # Clients may also be typed as a GSTIN, so a value outside the list is allowed.
            dv = DataValidation(type="list", formula1=f"=Lists!${source}$2:${source}${count + 1}",
                                allow_blank=True, showErrorMessage=column == "B")
            sheet.add_data_validation(dv)
            dv.add(f"{column}2:{column}{last_row}")
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
    "One row = one proforma invoice. Rows can be for one client or many.",
    "",
    "Client (required): the client's name exactly as in Project O2, or their 15-character GSTIN.",
    "Service (required): a service from the Services page. Its GST rate and SAC code are used.",
    "Amount (required): the value before GST, in rupees. GST is added automatically.",
    "Description (optional): leave empty to use the service's description.",
    "Due date (optional): day first, e.g. 31/10/2026. Empty means the end of this month.",
    f"TDS % (optional): the TDS the client will deduct. Empty means {DEFAULT_CLIENT_TDS_RATE:g}%.",
    "Interstate (optional): Yes if the client is in another state (IGST instead of CGST + SGST). Empty means No.",
    "Remarks (optional): internal note, not shown to the client.",
    "",
    f"At most {MAX_ROWS} rows per file. Nothing is created until every row is correct.",
    "Every row becomes a draft proforma and follows the normal approval flow.",
)
