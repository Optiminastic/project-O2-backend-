"""Indian finance identifiers: format rules and checksums, with no network.

One place for what a valid GSTIN, PAN, IFSC, CIN and the rest look like, so the
input schemas, the GSTIN lookup and the bulk upload all agree.
"""

import re

# 2 digit state code, 10 char PAN, entity number (1-9 or A-Z), "Z", checksum.
GSTIN_PATTERN = r"^[0-9]{2}[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
# The 4th character is the holder type: Company, Person, HUF, Firm, AOP, Trust, BOI, Local authority, AJP, Government.
PAN_PATTERN = r"^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$"
# 4 letter bank code, a zero, then the branch code.
IFSC_PATTERN = r"^[A-Z]{4}0[A-Z0-9]{6}$"
ACCOUNT_NUMBER_PATTERN = r"^[0-9]{9,18}$"
UDYAM_PATTERN = r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$"
# Company CIN (21 characters) or LLP identification number (AAA-1234).
CIN_PATTERN = r"^(?:[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}|[A-Z]{3}-[0-9]{4})$"
# Services are classified under SAC: 6 digits starting with 99.
SAC_PATTERN = r"^99[0-9]{4}$"
# GST rule 46: an invoice number is at most 16 letters, digits, "-" or "/".
INVOICE_NUMBER_PATTERN = r"^[A-Z0-9/-]{1,16}$"
# UTR / RRN / cheque number: NEFT 16, RTGS 22, IMPS 12, cheque 6.
BANK_REFERENCE_PATTERN = r"^[A-Z0-9]{6,22}$"
# Challan identification number or Form 16A certificate number.
TAX_DOCUMENT_PATTERN = r"^[A-Z0-9]{5,20}$"
# Indian mobile (optional +91 or 0), landline with STD code, or an international number.
PHONE_PATTERN = r"^(?:(?:\+91|0)?[6-9][0-9]{9}|0[1-9][0-9]{9}|\+(?!91)[1-9][0-9]{7,14})$"

GSTIN_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu", "26": "Dadra and Nagar Haveli",
    "27": "Maharashtra", "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}

_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(GSTIN_PATTERN)


def gstin_check_char(first14: str) -> str:
    """The 15th GSTIN character: the standard mod-36 checksum of the first 14."""
    total = 0
    for i, ch in enumerate(first14):
        product = _BASE36.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _BASE36[(36 - total % 36) % 36]


def gstin_error(gstin: str) -> str | None:
    """Why a GSTIN is not valid, or None when it is. Expects it trimmed and upper-cased."""
    if len(gstin) != 15:
        return "GSTIN must be exactly 15 characters."
    if not _GSTIN_RE.match(gstin):
        return "GSTIN format is wrong: 2 digit state code, 10 character PAN, entity number, Z, check character."
    if gstin[:2] not in GSTIN_STATE_CODES:
        return f"GSTIN starts with {gstin[:2]}, which is not a GST state code."
    if gstin_check_char(gstin[:14]) != gstin[14]:
        return "GSTIN check character does not match. Re-check the number for a typo."
    return None


def pan_of_gstin(gstin: str) -> str:
    """The PAN embedded in a GSTIN (characters 3 to 12)."""
    return gstin[2:12]
