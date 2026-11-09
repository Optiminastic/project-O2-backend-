"""GSTIN verification / auto-fill.

Two layers:
  1. Offline — validate the GSTIN format + checksum and derive the embedded
     PAN and the registration state. This always runs, needs no network or key.
  2. Live — if `settings.gst_api_key` is set, look the number up with
     GSTVerify (https://gstverify.co.in, GET /api/v1/verify/{GSTIN}, header
     `X-API-Key`) to fetch the legal name, trade name, registered address,
     status, taxpayer type, constitution and registration date.

     A GSTVerify *sandbox* key works the same way and returns sample data
     without spending credits. For local demos with no key at all, set
     GST_API_MOCK=true to get a canned response in GSTVerify's exact shape.

Nothing here is hardcoded: the provider URL and key both come from config / .env.
"""

import logging

import httpx
from fastapi import APIRouter, Depends

from app.config import settings
from app.core.deps import get_current_user
from app.models import User
from app.schemas.misc import GstLookupOut
from app.services.identifiers import GSTIN_STATE_CODES, gstin_error, pan_of_gstin

router = APIRouter(prefix="/gst", tags=["gst"])
log = logging.getLogger("app.gst")

SOURCE = "gstverify"
# Warn (API log + under the GST field) once the prepaid balance drops to this.
LOW_CREDITS = 3


def _address_from_gstverify(data: dict) -> str | None:
    """One-line address. GSTVerify returns `address` as a single string, usually
    already ending in the pincode; append the pincode only if it's missing."""
    address = (data.get("address") or "").strip().rstrip(",")
    pincode = str(data.get("pincode") or "").strip()
    if pincode and pincode not in address:
        address = f"{address} - {pincode}" if address else pincode
    return address or None


def _nature_of_business(value) -> list[str] | None:
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
        return items or None
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in value.split(",") if v.strip()]
    return None


def _mock_response(gstin: str) -> tuple[int, dict]:
    """Local-dev stand-in that mirrors GSTVerify's documented /verify response."""
    state_code = gstin[:2]
    return 200, {
        "success": True,
        "sandbox": True,
        "cached": False,
        "credits_remaining": 0,
        "data": {
            "gstin": gstin,
            "legal_name": "DEMO TRADERS PRIVATE LIMITED",
            "trade_name": "Demo Traders",
            "status": "Active",
            "constitution": "Private Limited Company",
            "taxpayer_type": "Regular",
            "registration_date": "01/07/2017",
            "last_updated": "12/10/2026",
            "state": GSTIN_STATE_CODES.get(state_code),
            "state_code": state_code,
            "pan": pan_of_gstin(gstin),
            "tan": None,
            "address": "4th Floor, Sunrise Business Park, Andheri East, Mumbai, 400069",
            "district": "Mumbai",
            "pincode": "400069",
            "nature_of_business": ["Supplier of Services", "Office / Sale Office"],
        },
    }


def _call_gstverify(gstin: str) -> tuple[int, dict]:
    if settings.gst_api_mock:
        return _mock_response(gstin)
    resp = httpx.get(
        f"{settings.gst_api_url.rstrip('/')}/{gstin}",
        headers={"X-API-Key": settings.gst_api_key, "Accept": "application/json"},
        timeout=15.0,
    )
    return resp.status_code, resp.json()


@router.get("/{gstin}", response_model=GstLookupOut)
def lookup_gstin(gstin: str, user: User = Depends(get_current_user)) -> GstLookupOut:
    gstin = gstin.strip().upper()

    # ---- offline validation + derivation ----
    error = gstin_error(gstin)
    if error:
        return GstLookupOut(gstin=gstin, valid=False, source="offline", message=error)

    state_code = gstin[:2]
    out = GstLookupOut(
        gstin=gstin,
        valid=True,
        pan=pan_of_gstin(gstin),
        state_code=state_code,
        state=GSTIN_STATE_CODES.get(state_code),
        source="offline",
    )

    # ---- live enrichment (only if a key is configured, or mock mode) ----
    if not (settings.gst_api_key or settings.gst_api_mock):
        out.message = "Verified format · add GST_API_KEY to fetch name & address."
        return out

    try:
        status_code, body = _call_gstverify(gstin)
    except (httpx.HTTPError, ValueError):
        out.message = "Details lookup unavailable right now (PAN & state derived offline)."
        return out

    data = body.get("data") if isinstance(body, dict) else None
    if not (status_code == 200 and isinstance(body, dict) and body.get("success") and isinstance(data, dict)):
        # GSTVerify errors: non-2xx with {"success": false, "error": "<message>"}
        err = body.get("error") if isinstance(body, dict) else None
        err_msg = err if isinstance(err, str) else (err.get("message") if isinstance(err, dict) else None)
        if status_code == 404:
            out.message = "GSTIN not found in the GST registry."
        elif status_code == 402:
            out.credits_remaining = 0
            log.warning("GSTVerify credits exhausted — top up at https://gstverify.co.in/pricing")
            out.message = "GSTVerify credits used up — top up the account to fetch name & address. PAN & state derived offline."
        elif status_code in (401, 403):
            out.message = "GST lookup is not available (check the GSTVerify key / credits). PAN & state derived offline."
        elif status_code == 429:
            out.message = "GST lookup rate limit reached — try again in a minute."
        elif status_code in (502, 503):
            out.message = "GST portal is not responding right now — try again shortly."
        else:
            out.message = err_msg or "GST lookup failed."
        return out

    out.source = SOURCE
    credits = body.get("credits_remaining")
    if isinstance(credits, int) and not settings.gst_api_mock:
        out.credits_remaining = credits
        log.info("GSTVerify lookup %s ok — %d credits remaining", gstin, credits)
        if credits <= LOW_CREDITS:
            log.warning("GSTVerify credits low: %d left", credits)
    out.legal_name = data.get("legal_name") or None
    out.trade_name = data.get("trade_name") or None
    out.status = data.get("status") or None
    out.address = _address_from_gstverify(data)
    out.taxpayer_type = data.get("taxpayer_type") or None
    out.constitution = data.get("constitution") or None
    out.registration_date = data.get("registration_date") or None
    out.cancellation_date = data.get("cancellation_date") or None
    out.nature_of_business = _nature_of_business(data.get("nature_of_business"))
    # GSTVerify's verify endpoint does not report e-invoice applicability.
    out.einvoice_enabled = data.get("einvoice_enabled")
    if data.get("pan"):
        out.pan = data["pan"]
    if data.get("state"):
        out.state = data["state"]
    if data.get("state_code"):
        out.state_code = str(data["state_code"])
    if not (out.legal_name or out.trade_name):
        # Seen on recently registered GSTINs: the registry returns the address,
        # status etc. but no name yet. Say so instead of silently leaving it blank.
        out.message = "GST registry has no business name for this GSTIN yet — enter it manually."
    elif body.get("sandbox") and not settings.gst_api_mock:
        out.message = "GSTVerify sandbox key — sample data, not the real registry."
    elif settings.gst_api_mock:
        out.message = "Demo data (mock mode) — not the real registry."
    return out
