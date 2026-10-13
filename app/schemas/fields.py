"""Validated field types for finance input, shared by every request schema.

Response schemas keep plain types on purpose: rows saved before these rules
existed must still load, and only new input has to meet them.
"""

import math
import re
from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field

from app.services import identifiers as ids
from app.services.numbering import business_date
from app.services.taxation import AMOUNT_DECIMALS, GST_RATE_SLABS, MAX_AMOUNT, MAX_TDS_RATE
# GST started on 1 July 2017; no GST invoice or bill can be older.
GST_START = date(2017, 7, 1)

NAME_MAX = 160
TEXT_MAX = 2000

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SEPARATORS = re.compile(r"[\s()-]")


# ---------- cleaning ----------


def blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _compact_upper(value: object) -> object:
    """Identifiers are typed by people: drop spaces and upper-case before checking."""
    return re.sub(r"\s", "", value).upper() if isinstance(value, str) else value


def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


def _phone_digits(value: object) -> object:
    return _SEPARATORS.sub("", value) if isinstance(value, str) else value


# ---------- identifiers ----------


def _matches(pattern: str, message: str):
    compiled = re.compile(pattern)

    def check(value: str) -> str:
        if not compiled.match(value):
            raise ValueError(message)
        return value

    return AfterValidator(check)


def _gstin(value: str) -> str:
    error = ids.gstin_error(value)
    if error:
        raise ValueError(error)
    return value


def _optional(kind):
    """Optional version of a field: an empty string means 'not given'."""
    return Annotated[kind | None, BeforeValidator(blank_to_none)]


Gstin = Annotated[str, BeforeValidator(_compact_upper), AfterValidator(_gstin)]
Pan = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.PAN_PATTERN, "PAN must be 10 characters like ABCDE1234F, with a valid holder type in the 4th place."),
]
Ifsc = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.IFSC_PATTERN, "IFSC must be 11 characters: 4 letters, a zero, then 6 letters or digits (e.g. HDFC0001234)."),
]
AccountNumber = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.ACCOUNT_NUMBER_PATTERN, "Bank account number must be 9 to 18 digits."),
]
UdyamNumber = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.UDYAM_PATTERN, "Udyam number must look like UDYAM-MH-26-0012345."),
]
Cin = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.CIN_PATTERN, "Enter a 21 character CIN (e.g. U72900MH2015PTC123456) or an LLPIN like AAB-1234."),
]
SacCode = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.SAC_PATTERN, "SAC code must be 6 digits starting with 99 (e.g. 998311)."),
]
InvoiceNumber = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.INVOICE_NUMBER_PATTERN, "Invoice number must be 1 to 16 letters, digits, '-' or '/' (GST rule 46)."),
]
BankReference = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.BANK_REFERENCE_PATTERN, "Bank reference (UTR, RRN or cheque number) must be 6 to 22 letters or digits."),
]
TaxDocumentNumber = Annotated[
    str,
    BeforeValidator(_compact_upper),
    _matches(ids.TAX_DOCUMENT_PATTERN, "Must be 5 to 20 letters or digits, with no spaces or symbols."),
]
Phone = Annotated[
    str,
    BeforeValidator(_phone_digits),
    _matches(ids.PHONE_PATTERN, "Phone must be a 10 digit Indian mobile, a landline with STD code, or +country code and number."),
]

OptionalGstin = _optional(Gstin)
OptionalPan = _optional(Pan)
OptionalIfsc = _optional(Ifsc)
OptionalAccountNumber = _optional(AccountNumber)
OptionalUdyam = _optional(UdyamNumber)
OptionalCin = _optional(Cin)
OptionalSacCode = _optional(SacCode)
OptionalBankReference = _optional(BankReference)
OptionalPhone = _optional(Phone)


# ---------- text ----------


def _no_control_chars(value: str) -> str:
    if _CONTROL_CHARS.search(value):
        raise ValueError("Contains hidden control characters. Retype it instead of pasting.")
    return value


def _single_line(value: str) -> str:
    if "\n" in value or "\r" in value or "\t" in value:
        raise ValueError("Must be on a single line.")
    return _no_control_chars(value)


Name = Annotated[
    str,
    BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v),
    Field(min_length=1, max_length=NAME_MAX),
    AfterValidator(_single_line),
]
LongText = Annotated[
    str,
    BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v),
    Field(min_length=1, max_length=TEXT_MAX),
    AfterValidator(_no_control_chars),
]
OptionalName = _optional(Name)
OptionalText = _optional(LongText)


# ---------- money and rates ----------


def _paise(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Must be a number.")
    if abs(round(value, AMOUNT_DECIMALS) - value) > 1e-9:
        raise ValueError("Amounts can have at most 2 decimal places (paise).")
    return round(value, AMOUNT_DECIMALS)


def _gst_slab(rate: float) -> float:
    if rate not in GST_RATE_SLABS:
        raise ValueError(f"GST rate must be one of {', '.join(f'{r:g}%' for r in GST_RATE_SLABS)}.")
    return rate


Money = Annotated[float, Field(ge=0, le=MAX_AMOUNT, allow_inf_nan=False), AfterValidator(_paise)]
PositiveMoney = Annotated[float, Field(gt=0, le=MAX_AMOUNT, allow_inf_nan=False), AfterValidator(_paise)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False), AfterValidator(_paise)]
GstRate = Annotated[float, Field(allow_inf_nan=False), AfterValidator(_gst_slab)]
TdsRate = Annotated[float, Field(ge=0, le=MAX_TDS_RATE, allow_inf_nan=False), AfterValidator(_paise)]


# ---------- dates ----------


def _not_future(value: date) -> date:
    if value > business_date():
        raise ValueError("Cannot be a future date.")
    return value


def _in_gst_era(value: date) -> date:
    if value < GST_START:
        raise ValueError(f"Cannot be before GST began on {GST_START:%d %b %Y}.")
    return value


# A date something already happened on: a payment, a bill, a deposit.
PastDate = Annotated[date, AfterValidator(_in_gst_era), AfterValidator(_not_future)]


# ---------- cross-field rules ----------


def check_pan_matches_gstin(pan: str | None, gstin: str | None) -> None:
    """A GSTIN carries its holder's PAN; when both are given they must agree."""
    if pan and gstin and ids.pan_of_gstin(gstin) != pan:
        raise ValueError(f"PAN {pan} does not match the PAN inside GSTIN {gstin} ({ids.pan_of_gstin(gstin)}).")


def check_date_order(start: date | None, end: date | None, start_label: str, end_label: str) -> None:
    if start and end and end < start:
        raise ValueError(f"{end_label} cannot be before {start_label}.")
