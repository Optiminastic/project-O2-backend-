"""Reading an uploaded spreadsheet (Excel or CSV) into rows keyed by column name.

Shared by the bulk uploads (invoices, services). Every problem with the file
itself is a 400 whose message tells the user what to change in the file.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from fastapi import HTTPException, status
from openpyxl import load_workbook

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500


@dataclass(frozen=True)
class TableSpec:
    """What an upload must look like: its columns, the sheet to read, and the word for one row."""

    required: tuple[str, ...]
    columns: tuple[str, ...]
    sheet_name: str
    row_noun: str  # plural, e.g. "invoices"
    max_rows: int = MAX_ROWS


def read_rows(spec: TableSpec, filename: str, content: bytes) -> list[dict[str, object]]:
    """Rows keyed by column name, in file order (blank rows kept so row numbers match). Raises 400 if unusable."""
    if len(content) > MAX_FILE_BYTES:
        raise bad_file("The file is larger than 2 MB. Split it into smaller uploads.")
    name = filename.lower()
    if name.endswith(".xlsx"):
        table = _xlsx_table(content, spec.sheet_name)
    elif name.endswith(".csv"):
        table = _csv_table(content)
    else:
        raise bad_file("Upload the template as an Excel (.xlsx) or CSV file.")
    if not table:
        raise bad_file("The file is empty. Use the template's header row.")

    headers = [str(h or "").strip().lower() for h in table[0]]
    missing = [c for c in spec.required if c.lower() not in headers]
    if missing:
        raise bad_file(f"Missing column(s): {', '.join(missing)}. Use the template's header row.")
    index = {c: headers.index(c.lower()) for c in spec.columns if c.lower() in headers}

    rows = [{c: values[i] if i < len(values) else None for c, i in index.items()} for values in table[1:]]
    filled = sum(has_data(r) for r in rows)
    if not filled:
        raise bad_file(f"The file has no {spec.row_noun} under the header.")
    if filled > spec.max_rows:
        raise bad_file(f"A single upload can hold at most {spec.max_rows} {spec.row_noun}. Split the file.")
    return rows


def has_data(record: dict[str, object]) -> bool:
    return any(text_of(v) for v in record.values())


def text_of(value: object) -> str:
    return "" if value is None else str(value).strip()


def bad_file(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


def _xlsx_table(content: bytes, sheet_name: str) -> list[list[object]]:
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - any parse failure means the same thing to the user
        raise bad_file("This Excel file could not be read. Save it as .xlsx and try again.")
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _csv_table(content: bytes) -> list[list[object]]:
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise bad_file("Save the CSV with UTF-8 encoding and try again.")
    return [r for r in csv.reader(io.StringIO(decoded))]
