"""Outgoing email: team invitations, client invoice links and vendor work orders.

If SMTP isn't configured (no ``smtp_host`` / ``smtp_from``), sending is skipped
gracefully and the caller falls back to surfacing the invite link directly in
the UI. This keeps these flows fully usable in local dev without secrets.
"""

import logging
import smtplib
from html import escape as html_escape
from email.message import EmailMessage
from email.utils import formataddr

from app.config import settings

logger = logging.getLogger("app.email")


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from_email)


def send_invite_email(
    *,
    to_email: str,
    to_name: str,
    accept_url: str,
    inviter_name: str,
    role_label: str,
) -> bool:
    """Send an invitation email. Returns True only if actually sent over SMTP."""
    subject = "You've been invited to Project O2"
    text = (
        f"Hi {to_name},\n\n"
        f"{inviter_name} has invited you to join the Project O2 finance workspace "
        f"as {role_label}.\n\n"
        f"Accept your invitation and set a password here:\n{accept_url}\n\n"
        f"This link will expire in {settings.invite_expire_hours // 24} days.\n\n"
        f"— Project O2"
    )
    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;color:#111">
  <h2 style="font-weight:600;margin:0 0 4px">You're invited to Project O2</h2>
  <p style="color:#555;margin:0 0 20px">A secure finance workspace by Optiminastic.</p>
  <p>Hi {to_name},</p>
  <p><strong>{inviter_name}</strong> has invited you to join as <strong>{role_label}</strong>.</p>
  <p style="margin:24px 0">
    <a href="{accept_url}"
       style="background:#111;color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;display:inline-block">
       Accept invitation
    </a>
  </p>
  <p style="color:#777;font-size:13px">Or paste this link into your browser:<br>
    <a href="{accept_url}" style="color:#555">{accept_url}</a></p>
  <p style="color:#999;font-size:12px;margin-top:24px">
    This link expires in {settings.invite_expire_hours // 24} days. If you weren't expecting this, you can ignore it.
  </p>
</div>"""

    if not smtp_configured():
        logger.warning("SMTP not configured — invite link for %s: %s", to_email, accept_url)
        return False
    return send_email(to_email=to_email, subject=subject, text=text, html=html)


Attachment = tuple[str, bytes, str]  # (filename, content, MIME type such as "application/pdf")


def send_email(
    *, to_email: str, subject: str, text: str, html: str, attachments: list[Attachment] | None = None
) -> bool:
    """Send one email. Returns True only if it was actually sent over SMTP."""
    if not smtp_configured():
        logger.warning("SMTP not configured; email to %s not sent (%s)", to_email, subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    msg["To"] = to_email
    if settings.smtp_reply_to:
        msg["Reply-To"] = settings.smtp_reply_to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for filename, content, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("Sent email to %s (%s)", to_email, subject)
        return True
    except Exception as exc:  # noqa: BLE001 - network/SMTP failures must not 500 the request
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return False


def send_invoice_link_email(
    *,
    to_email: str,
    client_name: str,
    document_label: str,
    document_number: str,
    total: str,
    link_url: str,
    expires_days: int,
    ask_for_approval: bool,
) -> bool:
    """Email a client a link to review a proforma or view an issued tax invoice."""
    sender = settings.company_legal_name or settings.smtp_from_name
    if ask_for_approval:
        subject = f"Please review {document_label} {document_number} from {sender}"
        intro = "Please review it and either approve it or tell us what to change."
        button = "Review and approve"
    else:
        subject = f"{document_label} {document_number} from {sender}"
        intro = "You can view, print or save it as a PDF from the link below."
        button = "View invoice"

    text = (
        f"Hi {client_name},\n\n"
        f"{sender} has shared {document_label} {document_number} for {total}.\n"
        f"{intro}\n\n{link_url}\n\n"
        f"This link expires in {expires_days} days.\n\n{sender}"
    )
    e = html_escape
    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;color:#111">
  <h2 style="font-weight:600;margin:0 0 4px">{e(document_label)} {e(document_number)}</h2>
  <p style="color:#555;margin:0 0 20px">From {e(sender)} · {e(total)}</p>
  <p>Hi {e(client_name)},</p>
  <p>{e(intro)}</p>
  <p style="margin:24px 0">
    <a href="{e(link_url)}"
       style="background:#111;color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;display:inline-block">
       {e(button)}
    </a>
  </p>
  <p style="color:#777;font-size:13px">Or paste this link into your browser:<br>
    <a href="{e(link_url)}" style="color:#555">{e(link_url)}</a></p>
  <p style="color:#999;font-size:12px;margin-top:24px">This link expires in {expires_days} days.</p>
</div>"""
    return send_email(to_email=to_email, subject=subject, text=text, html=html)


def send_work_order_email(
    *,
    to_email: str,
    vendor_name: str,
    work_order_number: str,
    project_label: str,
    agreed_cost: str,
    report_due: str,
    pdf: bytes,
    filename: str,
) -> bool:
    """Email a vendor their work order as a PDF attachment."""
    sender = settings.company_legal_name or settings.smtp_from_name
    subject = f"Work order {work_order_number} from {sender}: {project_label}"
    text = (
        f"Hi {vendor_name},\n\n"
        f"Please find attached work order {work_order_number} for {project_label}, "
        f"for {agreed_cost} plus GST. The report is due by {report_due}.\n\n"
        f"Please quote {work_order_number} on every invoice for this work.\n\n{sender}"
    )
    e = html_escape
    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;color:#111">
  <h2 style="font-weight:600;margin:0 0 4px">Work order {e(work_order_number)}</h2>
  <p style="color:#555;margin:0 0 20px">From {e(sender)} · {e(project_label)}</p>
  <p>Hi {e(vendor_name)},</p>
  <p>Please find the work order attached: <strong>{e(agreed_cost)}</strong> plus GST, report due by <strong>{e(report_due)}</strong>.</p>
  <p>Please quote <strong>{e(work_order_number)}</strong> on every invoice for this work.</p>
</div>"""
    return send_email(
        to_email=to_email, subject=subject, text=text, html=html,
        attachments=[(filename, pdf, "application/pdf")],
    )
