"""GSTIN verification / auto-fill.

Two layers:
  1. Offline — validate the GSTIN format + checksum and derive the embedded
     PAN and the registration state. This always runs, needs no network or key.
  2. Live — if `settings.gst_api_key` is set, look the number up with Appyflow
     to fetch the legal name, trade name and registered address.

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
