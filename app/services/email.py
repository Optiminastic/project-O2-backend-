"""Outgoing email — currently just team invitations.

If SMTP isn't configured (no ``smtp_host`` / ``smtp_from``), sending is skipped
gracefully and the caller falls back to surfacing the invite link directly in
the UI. This keeps the invite flow fully usable in local dev without secrets.
"""

import logging
import smtplib
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

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("Sent invite email to %s", to_email)
        return True
    except Exception as exc:  # noqa: BLE001 — network/SMTP failures shouldn't 500 the invite
        logger.error("Failed to send invite email to %s: %s", to_email, exc)
        return False
