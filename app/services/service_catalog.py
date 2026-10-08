"""Service catalogue rules shared by the services and invoices routers."""

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Service


def assert_title_free(db: Session, title: str, exclude_id: int | None = None) -> None:
    q = db.query(Service).filter(func.lower(Service.title) == title.lower())
    if exclude_id is not None:
        q = q.filter(Service.id != exclude_id)
    if q.first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"A service called '{title}' already exists")


def usable_service(db: Session, service_id: int) -> Service:
    """A service that may be put on an invoice: it must exist and be active."""
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    if not service.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{service.title}' is deactivated; choose another service")
    return service


def service_snapshot(service: Service, overrides: dict) -> dict:
    """Invoice fields copied from the service. Values the user typed on the invoice win."""
    def pick(field: str, default):
        value = overrides.get(field)
        return default if value is None or value == "" else value

    return {
        "service_id": service.id,
        "service_title": service.title,
        "service_description": pick("service_description", service.description),
        "sac_code": pick("sac_code", service.sac_code),
        "gst_rate": pick("gst_rate", service.gst_rate),
    }
