"""Erase Project O2's business data.

This is the most destructive operation in the product. It exists so the founder
can clear the platform, and it is written on the assumption that one day it will
be triggered by accident.

Three properties hold it together:

* **Nothing is deleted without a verified backup.** ``services.backup`` raises if
  it cannot produce and read back a dump, and this module treats any such
  failure as "stop".
* **The delete is one transaction in a fixed order.** The schema has no
  ``ON DELETE CASCADE`` anywhere, so an arbitrary order raises a foreign-key
  violation partway through and leaves the database half-erased.
* **Two tables survive.** See RETAINED below; both omissions are deliberate and
  load-bearing.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.database import Base
from app.models.enums import UserRole
from app.models.user import User
from app.services import backup as backup_svc
from app.services.agents import ensure_house_agent
from app.services.audit import log_action

logger = logging.getLogger("o2.wipe")

# The founder must type this exactly. Confirmed here as well as in the app, so
# a direct API call is held to the same standard as a tap in the UI.
CONFIRM_PHRASE = "ERASE PROJECT O2"

# Children before parents. There are no ON DELETE CASCADE constraints in this
# schema (only two SET NULLs on agent_id), so this order is load-bearing: get it
# wrong and Postgres raises a foreign-key violation mid-transaction.
DELETE_ORDER: tuple[str, ...] = (
    "bank_transactions",
    "approval_actions",
    "payment_approvals",
    "email_logs",
    "vendor_reports",
    "vendor_invoices",
    "payments",
    "invoice_links",
    "project_vendors",
    "projects",
    "vendor_allocations",
    "vendor_bank_accounts",
    "client_invoices",
    "services",
    "bank_statements",
    "vendors",
    "clients",
    "agents",
    "invitations",
)

# Deliberately not deleted.
#
#   users       Deleting every account re-opens unauthenticated CEO signup:
#               routers/auth.py makes the first account ever created an
#               ADMIN_CEO and only closes /auth/signup once a user exists. An
#               empty users table turns the finance system into a land grab.
#
#   audit_logs  services/audit.py writes nowhere else, so clearing this table
#               would erase the record that the erase happened.
#
#   document_sequences
#               Holds the last GST invoice number issued per financial year.
#               Resetting it would re-issue numbers already sent to clients,
#               breaking the unique, consecutive series GST Rule 46 requires.
RETAINED: tuple[str, ...] = ("users", "audit_logs", "document_sequences")

# Arbitrary but fixed: two concurrent wipes must not interleave their deletes.
_ADVISORY_LOCK_KEY = 8_142_003_991


def assert_covers_schema() -> None:
    """Fail loudly if a table is in the schema but in neither list.

    DELETE_ORDER is hand-maintained because the order matters and cannot be
    derived from the models. That makes it drift-prone: add a model, forget
    this file, and the erase quietly leaves a table full of data behind while
    reporting success. Checked before every preview and every erase, so the
    failure surfaces as a clear error rather than as silent partial deletion.
    """
    known = set(DELETE_ORDER) | set(RETAINED)
    actual = set(Base.metadata.tables.keys())

    unaccounted = actual - known
    if unaccounted:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Erase is disabled: these tables are not accounted for in wipe.py - "
            + ", ".join(sorted(unaccounted)),
        )

    missing = known - actual
    if missing:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Erase is disabled: wipe.py lists tables that do not exist - "
            + ", ".join(sorted(missing)),
        )


def preview(db: Session) -> dict[str, int]:
    """Row counts per table, so the caller can show what will be destroyed.

    Read-only. Includes the retained tables so the report is a complete picture
    of the database rather than only the part that disappears.
    """
    assert_covers_schema()
    counts: dict[str, int] = {}
    for table in DELETE_ORDER + RETAINED:
        counts[table] = db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
    return counts


def _authorise(db: Session, user: User, password: str, confirm: str) -> None:
    """Re-check every gate inside the service, not only on the route.

    The router already restricts this to ADMIN_CEO; repeating it here follows
    the same defence-in-depth pattern as services/approval.py, so the check
    cannot be lost by a future refactor of the route decorators.
    """
    if user.role != UserRole.ADMIN_CEO:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the founder can erase Project O2.")

    if confirm.strip() != CONFIRM_PHRASE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'Type "{CONFIRM_PHRASE}" exactly to confirm.',
        )

    # Re-authentication. Tokens last 12 hours and cannot be revoked, so a
    # signed-in session on a mislaid phone is not sufficient authority to
    # destroy the company's records.
    if not password or not verify_password(password, user.hashed_password):
        logger.warning("Rejected erase attempt by %s: wrong password.", user.email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That password is not correct.")


def erase_all(
    db: Session,
    user: User,
    *,
    password: str,
    confirm: str,
    reason: str = "Founder-initiated erase from the admin app",
) -> dict[str, object]:
    """Back up, then delete every business row. Returns a summary.

    Raises before deleting anything if any gate fails or the backup cannot be
    verified.
    """
    _authorise(db, user, password, confirm)

    # Serialise concurrent attempts. The xact variant releases on commit or
    # rollback, so a crashed request cannot leave the lock held.
    got_lock = db.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
    ).scalar_one()
    if not got_lock:
        raise HTTPException(status.HTTP_409_CONFLICT, "An erase is already running.")

    before = preview(db)

    # Logged before anything is destroyed, so the intent survives even if the
    # process dies mid-operation.
    logger.warning(
        "ERASE REQUESTED by %s (%s). Row counts before: %s",
        user.email,
        user.role.value,
        before,
    )

    # Fail closed: any BackupError propagates and the deletes below never run.
    manifest = backup_svc.create_backup(actor_email=user.email, reason=reason)

    deleted: dict[str, int] = {}
    for table in DELETE_ORDER:
        # DELETE rather than TRUNCATE ... RESTART IDENTITY: resetting the
        # sequences would make new invoice ids collide with ids already printed
        # on PDFs that clients have received.
        result = db.execute(text(f"DELETE FROM {table}"))
        deleted[table] = result.rowcount or 0

    # Clients cannot exist without an agent, so the in-house default comes straight back.
    ensure_house_agent(db)

    log_action(
        db,
        user,
        action="ERASE_ALL_DATA",
        entity_type="database",
        entity_id=None,
        detail=(
            f"Erased {sum(deleted.values())} rows across {len(DELETE_ORDER)} tables. "
            f"Backup: {manifest.reference} (sha256={manifest.sha256}). "
            f"Retained: {', '.join(RETAINED)}."
        ),
    )
    db.commit()

    logger.warning(
        "ERASE COMPLETED by %s. Deleted %s rows. Backup: %s",
        user.email,
        sum(deleted.values()),
        manifest.reference,
    )

    return {
        "deleted": deleted,
        "total_rows_deleted": sum(deleted.values()),
        "retained": list(RETAINED),
        "backup": {
            "reference": manifest.reference,
            "bytes": manifest.bytes,
            "sha256": manifest.sha256,
            "tables": manifest.table_count,
            "created_at": manifest.created_at,
        },
    }
