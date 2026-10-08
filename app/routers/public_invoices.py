"""Client-facing invoice links. No login: the unguessable token is the credential.

Every failure (unknown, expired, revoked or already-used token) returns the same
404, so a caller learns nothing by probing tokens.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import InvoiceLink
from app.schemas.invoice import (
    ClientApproveIn,
    ClientChangesIn,
    PublicClient,
    PublicCompany,
    PublicInvoice,
    PublicInvoiceView,
)
from app.services import invoice_links
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


def _company() -> PublicCompany:
    return PublicCompany(
        legal_name=settings.company_legal_name,
        address=settings.company_address,
        gstin=settings.company_gstin,
        state=settings.company_state,
        pan=settings.company_pan,
        email=settings.company_email,
        bank_name=settings.company_bank_name,
        bank_account=settings.company_bank_account,
        bank_ifsc=settings.company_bank_ifsc,
    )


def _view(link: InvoiceLink) -> PublicInvoiceView:
    return PublicInvoiceView(
        purpose=link.purpose,
        can_act=invoice_links.can_act(link),
        invoice=PublicInvoice.model_validate(link.invoice),
        client=PublicClient.model_validate(link.invoice.client),
        company=_company(),
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
