"""Encrypts plugin connection details (URL and auth headers) before they are stored.

The key is derived from the app's SECRET_KEY, so a database dump alone does not reveal plugin keys.
Changing SECRET_KEY makes stored plugins unreadable: they show as needing to be connected again.
"""

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_PURPOSE = b"o2-assistant-plugins:"


class SecretUnreadable(Exception):
    """The stored value was encrypted with a different SECRET_KEY, or is damaged."""


def _fernet() -> Fernet:
    digest = hashlib.sha256(_PURPOSE + settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(value: dict) -> str:
    return _fernet().encrypt(json.dumps(value).encode()).decode()


def unseal(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError) as exc:
        raise SecretUnreadable() from exc
