"""GSTIN verification / auto-fill.

Two layers:
  1. Offline — validate the GSTIN format + checksum and derive the embedded
     PAN and the registration state. This always runs, needs no network or key.
  2. Live — if `settings.gst_api_key` is set, look the number up with
     apiserver.in (POST /v1/gst/verify) to fetch the legal name, trade name,
     registered address, status, taxpayer type and e-invoice applicability.

Nothing here is hardcoded: the provider URL and key both come from config / .env.
"""

import httpx
from fastapi import APIRouter, Depends

from app.config import settings
from app.core.deps import get_current_user
from app.models import User
from app.schemas.misc import GstLookupOut
from app.services.identifiers import GSTIN_STATE_CODES, gstin_error, pan_of_gstin

router = APIRouter(prefix="/gst", tags=["gst"])


def _address_from_apiserver(place: dict | None) -> str | None:
    """Readable one-line address from apiserver.in's `principal_place` block."""
    if not isinstance(place, dict):
        return None
    parts = [
        place.get("line1"), place.get("locality"), place.get("city"),
        place.get("district"), place.get("state"),
    ]
    # De-duplicate (city and district are often the same) while keeping order.
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    joined = ", ".join(seen)
    if place.get("pincode"):
        joined = f"{joined} - {place['pincode']}" if joined else place["pincode"]
    return joined or None


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

    # ---- live enrichment (only if a key is configured) ----
    if not settings.gst_api_key:
        out.message = "Verified format · add GST_API_KEY to fetch name & address."
        return out

    try:
        resp = httpx.post(
            settings.gst_api_url,
            json={"gstin": gstin},
            headers={"Authorization": f"Bearer {settings.gst_api_key}"},
            timeout=15.0,
        )
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        out.message = "Details lookup unavailable right now (PAN & state derived offline)."
        return out

    data = body.get("data") if isinstance(body, dict) else None
    if not (isinstance(body, dict) and body.get("success") and isinstance(data, dict)):
        # apiserver.in errors: {"success": false, "error": {"code", "message", ...}}
        err = body.get("error") if isinstance(body, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        if code == "RECORD_NOT_FOUND":
            out.message = "GSTIN not found in the GST registry."
        elif code in ("AUTH_INVALID_KEY", "AUTH_SCOPE_DENIED", "WALLET_INSUFFICIENT_BALANCE"):
            out.message = "GST lookup is not available (check the apiserver.in key / wallet). PAN & state derived offline."
        else:
            out.message = (err.get("message") if isinstance(err, dict) else None) or "GST lookup failed."
        return out

    out.source = "apiserver"
    out.legal_name = data.get("legal_name") or None
    out.trade_name = data.get("trade_name") or None
    out.status = data.get("status") or None
    out.address = _address_from_apiserver(data.get("principal_place"))
    out.taxpayer_type = data.get("taxpayer_type") or None
    out.constitution = data.get("constitution") or None
    out.registration_date = data.get("registration_date") or None
    out.cancellation_date = data.get("cancellation_date") or None
    out.nature_of_business = data.get("nature_of_business") or None
    out.einvoice_enabled = data.get("einvoice_enabled")
    if data.get("pan"):
        out.pan = data["pan"]
    if data.get("state"):
        out.state = data["state"]
    return out
