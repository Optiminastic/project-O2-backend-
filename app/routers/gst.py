"""GSTIN verification / auto-fill.

Two layers:
  1. Offline — validate the GSTIN format + checksum and derive the embedded
     PAN and the registration state. This always runs, needs no network or key.
  2. Live — if `settings.gst_api_key` is set, look the number up with Appyflow
     to fetch the legal name, trade name and registered address.

Nothing here is hardcoded: the provider URL and key both come from config / .env.
"""

import re

import httpx
from fastapi import APIRouter, Depends

from app.config import settings
from app.core.deps import get_current_user
from app.models import User
from app.schemas.misc import GstLookupOut

router = APIRouter(prefix="/gst", tags=["gst"])

# 2 digit state code · 10 char PAN · 1 entity code · "Z" · 1 checksum.
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

# base-36 alphabet used by the GSTIN checksum
_CODE = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_STATE_CODES = {
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


def _checksum_ok(gstin: str) -> bool:
    """Verify the 15th character against the standard GSTIN mod-36 checksum."""
    total = 0
    for i, ch in enumerate(gstin[:14]):
        val = _CODE.index(ch)
        factor = 2 if i % 2 else 1
        product = val * factor
        total += product // 36 + product % 36
    check = _CODE[(36 - (total % 36)) % 36]
    return check == gstin[14]


def _address_from_appyflow(pradr: dict | None) -> str | None:
    """Best-effort readable address from Appyflow's principal-address block."""
    if not isinstance(pradr, dict):
        return None
    if pradr.get("adr"):
        return pradr["adr"]
    addr = pradr.get("addr")
    if isinstance(addr, dict):
        parts = [
            addr.get("bno"), addr.get("bnm"), addr.get("st"), addr.get("loc"),
            addr.get("dst"), addr.get("stcd"), addr.get("pncd"),
        ]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    return None


@router.get("/{gstin}", response_model=GstLookupOut)
def lookup_gstin(gstin: str, user: User = Depends(get_current_user)) -> GstLookupOut:
    gstin = gstin.strip().upper()

    # ---- offline validation + derivation ----
    if not _GSTIN_RE.match(gstin) or not _checksum_ok(gstin):
        return GstLookupOut(
            gstin=gstin, valid=False, source="offline",
            message="Not a valid GSTIN (check the 15-character format).",
        )

    state_code = gstin[:2]
    out = GstLookupOut(
        gstin=gstin,
        valid=True,
        pan=gstin[2:12],
        state_code=state_code,
        state=_STATE_CODES.get(state_code),
        source="offline",
    )

    # ---- live enrichment (only if a key is configured) ----
    if not settings.gst_api_key:
        out.message = "Verified format · add GST_API_KEY to fetch name & address."
        return out

    try:
        resp = httpx.get(
            settings.gst_api_url,
            params={"gstNo": gstin, "key_secret": settings.gst_api_key},
            timeout=10.0,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        out.message = "Details lookup unavailable right now (PAN & state derived offline)."
        return out

    info = data.get("taxpayerInfo") if isinstance(data, dict) else None
    if not info:
        # Appyflow returns {"error": true, "message": "..."} or a bare message.
        out.message = (data.get("message") if isinstance(data, dict) else None) or "GSTIN not found in the registry."
        return out

    out.source = "appyflow"
    out.legal_name = info.get("lgnm") or None
    out.trade_name = info.get("tradeNam") or None
    out.status = info.get("sts") or None
    out.address = _address_from_appyflow(info.get("pradr"))
    if info.get("pan"):
        out.pan = info["pan"]
    return out
