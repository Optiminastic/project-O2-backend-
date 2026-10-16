from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles
from app.core.rbac import NON_EXEC
from app.database import get_db
from app.models import ClientInvoice, Project, Service, User
from app.schemas.service import (
    ServiceBulkCreatedOut,
    ServiceBulkPreviewOut,
    ServiceBulkRowOut,
    ServiceCreate,
    ServiceOut,
    ServiceUpdate,
)
from app.services import bulk_services as bulk
from app.services.audit import log_action
from app.services.service_catalog import assert_title_free
from app.services.spreadsheet import MAX_FILE_BYTES

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


# ---------- Bulk upload ----------

TEMPLATE_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/bulk/template")
def bulk_template(user: User = Depends(require_roles(*NON_EXEC))):
    return Response(
        content=bulk.build_template(),
        media_type=TEMPLATE_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="bulk-services-template.xlsx"'},
    )


async def _checked_upload(db: Session, file: UploadFile) -> list[bulk.ServiceRowCheck]:
    content = await file.read(MAX_FILE_BYTES + 1)
    return bulk.check_rows(db, bulk.read_rows(file.filename or "", content))


@router.post("/bulk/preview", response_model=ServiceBulkPreviewOut)
async def bulk_preview(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    """Check an upload without creating anything."""
    checks = await _checked_upload(db, file)
    rows = [ServiceBulkRowOut(**vars(c)) for c in checks]
    error_count = sum(1 for r in rows if r.errors)
    return ServiceBulkPreviewOut(rows=rows, ready=error_count == 0, service_count=len(rows), error_count=error_count)


@router.post("/bulk", response_model=ServiceBulkCreatedOut, status_code=201)
async def bulk_create(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    """Create every service in the file in one transaction, or none if any row has an error."""
    checks = await _checked_upload(db, file)
    bad = sum(1 for c in checks if c.errors)
    if bad:
        noun = "row has" if bad == 1 else "rows have"
        raise HTTPException(422, f"Nothing was created: {bad} {noun} problems. Fix them and upload again.")
    services = [Service(title=c.title, sac_code=c.sac_code) for c in checks]
    db.add_all(services)
    db.flush()
    log_action(db, user, "Bulk uploaded services", "Service", None, f"{len(services)} services")
    db.commit()
    for s in services:
        db.refresh(s)
    return ServiceBulkCreatedOut(created=len(services), services=services)


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
    projects = db.query(Project).filter(Project.service_id == service.id).count()
    if projects:
        raise HTTPException(
            409, f"'{service.title}' is the service of {projects} project(s). Deactivate it instead."
        )
    db.delete(service)
    log_action(db, user, "Deleted service", "Service", service_id, service.title)
    db.commit()
