from sqlalchemy.orm import Session

from app.models import AuditLog, User


def log_action(
    db: Session,
    user: User | None,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    detail: str | None = None,
) -> None:
    """Append an entry to the cross-cutting audit trail. Caller commits."""
    db.add(
        AuditLog(
            actor_name=user.name if user else None,
            actor_role=user.role.value if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
