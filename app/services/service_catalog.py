"""Service catalogue rules shared by the services, projects and invoices routers."""

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Project, Service


def assert_title_free(db: Session, title: str, exclude_id: int | None = None) -> None:
    q = db.query(Service).filter(func.lower(Service.title) == title.lower())
    if exclude_id is not None:
        q = q.filter(Service.id != exclude_id)
    if q.first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"A service called '{title}' already exists")


def sac_taken_by(db: Session, sac_code: str | None, exclude_id: int | None = None) -> Service | None:
    """The other service that already carries this SAC code, if any."""
    if not sac_code:
        return None
    q = db.query(Service).filter(Service.sac_code == sac_code)
    if exclude_id is not None:
        q = q.filter(Service.id != exclude_id)
    return q.first()


def assert_sac_free(db: Session, sac_code: str | None, exclude_id: int | None = None) -> None:
    """Each SAC code belongs to one service in the catalogue."""
    other = sac_taken_by(db, sac_code, exclude_id)
    if other:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"SAC {sac_code} is already used by '{other.title}'. Each service needs its own SAC code."
        )


def usable_service(db: Session, service_id: int) -> Service:
    """A service that may be put on an invoice: it must exist and be active."""
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    if not service.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{service.title}' is deactivated; choose another service")
    return service


def usable_project(db: Session, project_id: int) -> Project:
    """A project that may be invoiced: it must exist and have a client and at least one service."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if project.client_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.code} has no client. Add one before invoicing.")
    if not project.services:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Project {project.code} has no service. Choose one before invoicing."
        )
    return project


def usable_services(db: Session, service_ids: list[int]) -> list[Service]:
    """Active catalogue services for a project, in the order given, each once."""
    unique = list(dict.fromkeys(service_ids))
    if not unique:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose at least one service.")
    return [usable_service(db, sid) for sid in unique]
