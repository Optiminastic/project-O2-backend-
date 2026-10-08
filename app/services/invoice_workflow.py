"""Client invoice lifecycle: proforma -> client approval -> manager issue.

    Draft --send--> Awaiting Client --client approves--> Client Approved --issue--> Sent
      ^                   |                                     |
      +--- edit <-- Changes Requested <-------- return ---------+

Only the functions here move an invoice between these stages. Each one records
an audit entry; the caller commits.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ClientApprovalMethod,
    ClientInvoice,
    InvoiceLink,
    InvoiceLinkPurpose,
    InvoiceStatus,
    User,
    UserRole,
)
from app.services import invoice_links
from app.services.audit import log_action
from app.services.email import send_invoice_link_email
from app.services.numbering import TAX_INVOICE_PREFIX, business_date, month_end, next_number

ENTITY = "ClientInvoice"
ISSUER_ROLES = frozenset({UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER})
SENDABLE = frozenset({InvoiceStatus.DRAFT, InvoiceStatus.CHANGES_REQUESTED, InvoiceStatus.AWAITING_CLIENT})
EDITABLE = SENDABLE
DELETABLE = frozenset({InvoiceStatus.DRAFT, InvoiceStatus.CHANGES_REQUESTED})
# Statuses a user may still set by hand on an issued invoice. Payment statuses
# are derived from payments and workflow stages only move through this module.
MANUAL_STATUSES = frozenset(
    {InvoiceStatus.SENT, InvoiceStatus.PENDING, InvoiceStatus.OVERDUE, InvoiceStatus.DISPUTED, InvoiceStatus.CANCELLED}
)


@dataclass
class LinkDelivery:
    url: str
    emailed: bool


def _require_stage(invoice: ClientInvoice, allowed: frozenset[InvoiceStatus], action: str) -> None:
    if invoice.status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot {action} an invoice in '{invoice.status.value}'")


def _require_issuer(invoice: ClientInvoice, user: User) -> None:
    if user.role not in ISSUER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a Finance Manager, CFO or CEO can do this")
    if invoice.created_by_id is not None and invoice.created_by_id == user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You created this invoice, so a different manager must approve it (maker-checker).",
        )


def format_inr(amount: float) -> str:
    """Indian digit grouping: 118000 -> '₹1,18,000.00'."""
    whole, fraction = f"{abs(amount):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join(groups + [tail]) if groups else tail
    return f"{'-' if amount < 0 else ''}₹{grouped}.{fraction}"


def _deliver(db: Session, invoice: ClientInvoice, purpose: InvoiceLinkPurpose) -> LinkDelivery:
    link = invoice_links.create_link(db, invoice, purpose)
    url = invoice_links.link_url(link.token)
    is_proforma = purpose == InvoiceLinkPurpose.APPROVE
    emailed = send_invoice_link_email(
        to_email=invoice.client.email,
        client_name=invoice.client.business_name,
        document_label="Proforma invoice" if is_proforma else "Tax invoice",
        document_number=invoice.display_number,
        total=format_inr(invoice.total_amount),
        link_url=url,
        expires_days=invoice_links.lifetime_days(purpose),
        ask_for_approval=is_proforma,
    )
    return LinkDelivery(url=url, emailed=emailed)


def before_edit(db: Session, invoice: ClientInvoice, user: User) -> None:
    """Guard a proforma edit. Editing what the client is reviewing withdraws it."""
    if invoice.is_issued:
        return  # issued invoices keep the existing payment-lock rules
    _require_stage(invoice, EDITABLE, "edit")
    if invoice.status == InvoiceStatus.AWAITING_CLIENT:
        invoice_links.revoke_open_links(db, invoice, InvoiceLinkPurpose.APPROVE)
        invoice.status = InvoiceStatus.DRAFT
        log_action(db, user, "Edited proforma; client approval link withdrawn", ENTITY, invoice.id)


def send_to_client(db: Session, invoice: ClientInvoice, user: User) -> LinkDelivery:
    _require_stage(invoice, SENDABLE, "send")
    invoice_links.revoke_open_links(db, invoice, InvoiceLinkPurpose.APPROVE)
    invoice.status = InvoiceStatus.AWAITING_CLIENT
    delivery = _deliver(db, invoice, InvoiceLinkPurpose.APPROVE)
    log_action(
        db, user, "Sent proforma to client", ENTITY, invoice.id,
        f"to {invoice.client.email}" + ("" if delivery.emailed else " (email not configured; link shared manually)"),
    )
    return delivery


def _mark_client_approved(invoice: ClientInvoice, method: ClientApprovalMethod, approver: str, note: str | None) -> None:
    invoice.status = InvoiceStatus.CLIENT_APPROVED
    invoice.client_approved_at = datetime.now(timezone.utc)
    invoice.client_approval_method = method
    invoice.client_approver_name = approver
    invoice.client_approval_note = note
    invoice.change_request_note = None
    invoice.change_requested_by = None


def approve_via_link(db: Session, link: InvoiceLink, approver_name: str) -> None:
    invoice = link.invoice
    link.used_at = datetime.now(timezone.utc)
    _mark_client_approved(invoice, ClientApprovalMethod.LINK, approver_name, None)
    log_action(
        db, None, "Client approved proforma", ENTITY, invoice.id,
        actor_name=f"{approver_name} (client)", actor_role="CLIENT",
    )


def request_changes_via_link(db: Session, link: InvoiceLink, comment: str) -> None:
    invoice = link.invoice
    link.used_at = datetime.now(timezone.utc)
    invoice.status = InvoiceStatus.CHANGES_REQUESTED
    invoice.change_request_note = comment
    invoice.change_requested_by = "Client"
    log_action(
        db, None, "Client requested changes", ENTITY, invoice.id, comment,
        actor_name=f"{invoice.client.business_name} (client)", actor_role="CLIENT",
    )


def approve_manually(db: Session, invoice: ClientInvoice, user: User, note: str) -> None:
    _require_stage(invoice, SENDABLE, "record client approval for")
    invoice_links.revoke_open_links(db, invoice, InvoiceLinkPurpose.APPROVE)
    _mark_client_approved(invoice, ClientApprovalMethod.MANUAL, invoice.client.business_name, note)
    log_action(db, user, "Recorded client approval (manual)", ENTITY, invoice.id, note)


def _assert_company_ready() -> None:
    missing = [
        name
        for name, value in (("COMPANY_LEGAL_NAME", settings.company_legal_name), ("COMPANY_GSTIN", settings.company_gstin))
        if not value.strip()
    ]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A tax invoice needs your company's details. Set {', '.join(missing)} in the backend settings first.",
        )


def issue(db: Session, invoice: ClientInvoice, user: User) -> LinkDelivery:
    """Turn a client-approved proforma into a numbered, locked GST tax invoice."""
    _require_issuer(invoice, user)
    _require_stage(invoice, frozenset({InvoiceStatus.CLIENT_APPROVED}), "issue")
    _assert_company_ready()

    now = datetime.now(timezone.utc)
    today = business_date(now)
    invoice.invoice_number = next_number(db, TAX_INVOICE_PREFIX, today)
    invoice.invoice_date = today
    # A proforma raised last month may carry a month-end that has passed; a new
    # tax invoice must not go out already overdue.
    if invoice.due_date is None or invoice.due_date < today:
        invoice.due_date = month_end(today)
    invoice.status = InvoiceStatus.SENT
    invoice.issued_by_id = user.id
    invoice.issued_at = now
    invoice.is_locked = True
    invoice.locked_at = now
    delivery = _deliver(db, invoice, InvoiceLinkPurpose.VIEW)
    log_action(
        db, user, "Issued tax invoice", ENTITY, invoice.id,
        f"{invoice.invoice_number} from {invoice.proforma_number or 'legacy draft'}"
        + ("" if delivery.emailed else " (email not configured; link shared manually)"),
    )
    return delivery


def return_for_changes(db: Session, invoice: ClientInvoice, user: User, reason: str) -> None:
    _require_issuer(invoice, user)
    _require_stage(invoice, frozenset({InvoiceStatus.CLIENT_APPROVED}), "return")
    invoice.status = InvoiceStatus.CHANGES_REQUESTED
    invoice.change_request_note = reason
    invoice.change_requested_by = user.name
    invoice.client_approved_at = None
    invoice.client_approval_method = None
    invoice.client_approver_name = None
    invoice.client_approval_note = None
    log_action(db, user, "Returned for changes", ENTITY, invoice.id, reason)


def assert_manual_status(invoice: ClientInvoice, new_status: InvoiceStatus) -> None:
    if new_status == invoice.status:
        return
    if not invoice.is_issued or new_status not in MANUAL_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invoice stages change only through the approval workflow (send, approve, issue).",
        )
