"""Secure client links for invoices (``/i/<token>``).

A link is valid while it is neither revoked nor expired. An approve link can be
acted on once, and only while its invoice is waiting for the client.
"""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ClientInvoice, InvoiceLink, InvoiceLinkPurpose, InvoiceStatus

TOKEN_BYTES = 32


def _lifetime(purpose: InvoiceLinkPurpose) -> timedelta:
    days = (
        settings.invoice_approval_link_days
        if purpose == InvoiceLinkPurpose.APPROVE
        else settings.invoice_view_link_days
    )
    return timedelta(days=days)


def lifetime_days(purpose: InvoiceLinkPurpose) -> int:
    return _lifetime(purpose).days


def link_url(token: str) -> str:
    return f"{settings.frontend_origin.rstrip('/')}/i/{token}"


def create_link(db: Session, invoice: ClientInvoice, purpose: InvoiceLinkPurpose) -> InvoiceLink:
    link = InvoiceLink(
        invoice_id=invoice.id,
        token=secrets.token_urlsafe(TOKEN_BYTES),
        purpose=purpose,
        expires_at=datetime.now(timezone.utc) + _lifetime(purpose),
    )
    db.add(link)
    return link


def revoke_open_links(db: Session, invoice: ClientInvoice, purpose: InvoiceLinkPurpose) -> None:
    now = datetime.now(timezone.utc)
    (
        db.query(InvoiceLink)
        .filter(
            InvoiceLink.invoice_id == invoice.id,
            InvoiceLink.purpose == purpose,
            InvoiceLink.revoked_at.is_(None),
        )
        .update({InvoiceLink.revoked_at: now}, synchronize_session=False)
    )


def find_live_link(db: Session, token: str) -> InvoiceLink | None:
    """The link for this token if it can still be opened, else None."""
    link = db.query(InvoiceLink).filter(InvoiceLink.token == token).first()
    if link is None or link.revoked_at is not None:
        return None
    if link.expires_at <= datetime.now(timezone.utc):
        return None
    return link


def can_act(link: InvoiceLink) -> bool:
    """Whether the client may still approve or request changes through this link."""
    return (
        link.purpose == InvoiceLinkPurpose.APPROVE
        and link.used_at is None
        and link.invoice.status == InvoiceStatus.AWAITING_CLIENT
    )
