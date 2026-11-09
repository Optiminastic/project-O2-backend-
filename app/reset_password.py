"""Set a new password for an existing user, from the server, when nobody can sign in.

Run inside the API container:

    docker compose exec api python -m app.reset_password

It lists the CEO accounts, asks for the email and the new password (typed twice,
never shown or logged), and reactivates the account. Nothing else changes.
"""

import getpass
import sys

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import User, UserRole

PASSWORD_MIN = 8
PASSWORD_MAX = 128


class ResetError(ValueError):
    """A reset that cannot be done, with a message for the person at the terminal."""


def ceo_accounts(db: Session) -> list[User]:
    return db.query(User).filter(User.role == UserRole.ADMIN_CEO).order_by(User.email).all()


def reset_password(db: Session, email: str, password: str) -> User:
    """Give ``email`` the new ``password`` and reactivate the account. The caller commits."""
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise ResetError(f"The password must be {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if user is None:
        raise ResetError(f"No account with the email {email.strip()}.")
    user.hashed_password = hash_password(password)
    user.is_active = True
    return user


def main() -> int:
    with SessionLocal() as db:
        print("CEO accounts:")
        for user in ceo_accounts(db):
            print(f"  {user.email}{'' if user.is_active else '  (deactivated)'}")
        email = input("Email to reset: ")
        password = getpass.getpass("New password: ")
        if getpass.getpass("Type it again: ") != password:
            print("The passwords did not match. Nothing was changed.")
            return 1
        try:
            user = reset_password(db, email, password)
        except ResetError as exc:
            print(f"{exc} Nothing was changed.")
            return 1
        db.commit()
        print(f"Password reset for {user.email}. Sign in with it now.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
