"""Founder-only destructive administration.

One capability: erase Project O2's business data. Split into a read-only
preview and the erase itself, so the caller can show what will be destroyed
before asking anyone to confirm it.

Guarded by ``require_ceo`` here and re-checked inside ``services.wipe``.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import require_ceo
from app.database import get_db
from app.models import User
from app.services import wipe as wipe_svc

router = APIRouter(prefix="/admin", tags=["admin"])


class WipePreviewOut(BaseModel):
    """What an erase would destroy, and what it would leave behind."""

    counts: dict[str, int]
    will_delete: list[str]
    will_retain: list[str]
    total_rows_to_delete: int
    confirm_phrase: str


class WipeRequest(BaseModel):
    # Re-authentication: a 12-hour token that cannot be revoked is not enough
    # authority on its own to destroy the company's financial records.
    password: str = Field(min_length=1)
    confirm: str = Field(min_length=1)


@router.get("/wipe/preview", response_model=WipePreviewOut)
def wipe_preview(
    db: Session = Depends(get_db),
    user: User = Depends(require_ceo),
) -> WipePreviewOut:
    counts = wipe_svc.preview(db)
    return WipePreviewOut(
        counts=counts,
        will_delete=list(wipe_svc.DELETE_ORDER),
        will_retain=list(wipe_svc.RETAINED),
        total_rows_to_delete=sum(counts[t] for t in wipe_svc.DELETE_ORDER),
        confirm_phrase=wipe_svc.CONFIRM_PHRASE,
    )


@router.post("/wipe")
def wipe(
    payload: WipeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_ceo),
) -> dict[str, object]:
    """Back up, then erase. Raises before deleting if any check fails."""
    return wipe_svc.erase_all(
        db,
        user,
        password=payload.password,
        confirm=payload.confirm,
    )
