"""Client-facing invoice links. No login: the unguessable token is the credential.

Every failure (unknown, expired, revoked or already-used token) returns the same
404, so a caller learns nothing by probing tokens.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import InvoiceLink
from app.schemas.invoice import (
    ClientApproveIn,
    ClientChangesIn,
    PublicInvoice,
    PublicInvoiceView,
)
from app.services import invoice_document, invoice_links
from app.services import invoice_workflow as workflow

router = APIRouter(prefix="/public/invoices", tags=["public"])

NOT_FOUND = "This link is invalid or has expired. Please contact the sender for a new one."


def _live_link(db: Session, token: str) -> InvoiceLink:
    link = invoice_links.find_live_link(db, token)
    if link is None:
        raise HTTPException(404, NOT_FOUND)
    return link


def _actionable_link(db: Session, token: str) -> InvoiceLink:
    link = _live_link(db, token)
    if not invoice_links.can_act(link):
        raise HTTPException(404, NOT_FOUND)
    return link


def _view(link: InvoiceLink) -> PublicInvoiceView:
    return PublicInvoiceView(
        **invoice_document.document(link.invoice).model_dump(),
        purpose=link.purpose,
        can_act=invoice_links.can_act(link),
    )


@router.get("/{token}", response_model=PublicInvoiceView)
def view_invoice(token: str, db: Session = Depends(get_db)):
    return _view(_live_link(db, token))


@router.post("/{token}/approve", response_model=PublicInvoice)
def approve(token: str, payload: ClientApproveIn, db: Session = Depends(get_db)):
    link = _actionable_link(db, token)
    workflow.approve_via_link(db, link, payload.name)
    db.commit()
    return PublicInvoice.model_validate(link.invoice)


@router.post("/{token}/request-changes", response_model=PublicInvoice)
def request_changes(token: str, payload: ClientChangesIn, db: Session = Depends(get_db)):
    link = _actionable_link(db, token)
    workflow.request_changes_via_link(db, link, payload.comment)
    db.commit()
    return PublicInvoice.model_validate(link.invoice)
