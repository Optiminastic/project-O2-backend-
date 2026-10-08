from sqlalchemy.orm import Session

from app.models import AuditLog, User


def log_action(
    db: Session,
    user: User | None,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    detail: str | None = None,
    *,
    actor_name: str | None = None,
    actor_role: str | None = None,
) -> None:
    """Append an entry to the cross-cutting audit trail. Caller commits.

    ``actor_name`` / ``actor_role`` describe an actor without an account, such as
    a client approving through an invoice link. They are used only when ``user`` is None.
    """
    db.add(
        AuditLog(
            actor_name=user.name if user else actor_name,
            actor_role=user.role.value if user else actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
