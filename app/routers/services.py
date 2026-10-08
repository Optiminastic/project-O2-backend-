from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles
from app.core.rbac import NON_EXEC
from app.database import get_db
from app.models import ClientInvoice, Service, User
from app.schemas.service import ServiceCreate, ServiceOut, ServiceUpdate
from app.services.audit import log_action
from app.services.service_catalog import assert_title_free

router = APIRouter(prefix="/services", tags=["services"])


def _get(db: Session, service_id: int) -> Service:
    service = db.get(Service, service_id)
    if not service:
        raise HTTPException(404, "Service not found")
    return service


@router.get("", response_model=list[ServiceOut])
def list_services(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Active services for the invoice dropdown; managers may include deactivated ones."""
    q = db.query(Service)
    if not (include_inactive and user.role in NON_EXEC):
        q = q.filter(Service.is_active.is_(True))
    return q.order_by(Service.title).all()


@router.post("", response_model=ServiceOut, status_code=201)
def create_service(payload: ServiceCreate, db: Session = Depends(get_db), user: User = Depends(require_roles(*NON_EXEC))):
    assert_title_free(db, payload.title)
    service = Service(**payload.model_dump())
    db.add(service)
    db.flush()
    log_action(db, user, "Created service", "Service", service.id, service.title)
    db.commit()
    db.refresh(service)
    return service


@router.patch("/{service_id}", response_model=ServiceOut)
def update_service(
    service_id: int,
    payload: ServiceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    service = _get(db, service_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("title"):
        assert_title_free(db, changes["title"], exclude_id=service.id)
    for field, value in changes.items():
        setattr(service, field, value)
    # Existing invoices keep the details they were raised with.
    log_action(db, user, "Updated service", "Service", service.id, ", ".join(sorted(changes)) or None)
    db.commit()
    db.refresh(service)
    return service


@router.delete("/{service_id}", status_code=204)
def delete_service(service_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*NON_EXEC))):
    service = _get(db, service_id)
    used = db.query(ClientInvoice).filter(ClientInvoice.service_id == service.id).count()
    if used:
        raise HTTPException(
            409, f"'{service.title}' is used on {used} invoice(s) and must stay on record. Deactivate it instead."
        )
    db.delete(service)
    log_action(db, user, "Deleted service", "Service", service_id, service.title)
    db.commit()
