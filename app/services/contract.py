"""A project's contract value caps what may be billed on it.

The cap applies to proformas as they are raised or edited. Cancelled invoices
do not count. A contract value of 0 means it is not known, so nothing is capped.
"""

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ClientInvoice, InvoiceStatus, Project
from app.services.invoice_workflow import format_inr

# Paise rounding on line totals must not trip the cap.
TOLERANCE = 0.005


def billed_elsewhere(db: Session, project_id: int, exclude_invoice_id: int | None = None) -> float:
    """Taxable value already billed on the project, leaving out one invoice (the one being edited)."""
    q = db.query(func.coalesce(func.sum(ClientInvoice.taxable_value), 0.0)).filter(
        ClientInvoice.project_id == project_id, ClientInvoice.status != InvoiceStatus.CANCELLED
    )
    if exclude_invoice_id is not None:
        q = q.filter(ClientInvoice.id != exclude_invoice_id)
    return round(float(q.scalar() or 0.0), 2)


def over_contract_message(project: Project, already_billed: float, amount: float) -> str | None:
    """Why ``amount`` cannot be billed on ``project``, or None when it fits."""
    contract = project.contract_value or 0.0
    if contract <= 0:
        return None
    left = round(contract - already_billed, 2)
    if amount <= left + TOLERANCE:
        return None
    return (
        f"This takes {project.code} {format_inr(amount - left)} past its contract value of {format_inr(contract)} "
        f"({format_inr(max(left, 0))} left to bill). Lower the amount or raise the project's contract value."
    )


def assert_within_contract(db: Session, project: Project, amount: float, exclude_invoice_id: int | None = None) -> None:
    message = over_contract_message(project, billed_elsewhere(db, project.id, exclude_invoice_id), amount)
    if message:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, message)
