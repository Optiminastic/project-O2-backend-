"""Workspace email-domain policy.

Every account (the CEO who signs up, and everyone they invite) must use an email
on the configured workspace domain — e.g. ``name@optiminastic.com``.
"""

from fastapi import HTTPException

from app.config import settings


def normalize_workspace_email(email: str) -> str:
    """Lower-case + trim the email and enforce the workspace domain.

    Raises 400 if the address isn't on the workspace domain.
    """
    cleaned = email.strip().lower()
    domain = settings.workspace_email_domain.strip().lower()
    if not cleaned.endswith("@" + domain):
        raise HTTPException(
            status_code=400,
            detail=f"Use your work email — only @{domain} addresses can join this workspace.",
        )
    return cleaned
