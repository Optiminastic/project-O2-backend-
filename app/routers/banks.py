from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles
from app.core.rbac import NON_EXEC
from app.database import get_db
from app.models import CompanyBank, Payment, User
from app.schemas.bank import BankCreate, BankOut, BankUpdate
from app.services.audit import log_action

router = APIRouter(prefix="/banks", tags=["banks"])


def _get(db: Session, bank_id: int) -> CompanyBank:
    bank = db.get(CompanyBank, bank_id)
    if not bank:
        raise HTTPException(404, "Bank not found")
    return bank


def _assert_new(db: Session, name: str, last4: str, exclude_id: int | None = None) -> None:
    q = db.query(CompanyBank).filter(func.lower(CompanyBank.name) == name.lower(), CompanyBank.account_last4 == last4)
    if exclude_id is not None:
        q = q.filter(CompanyBank.id != exclude_id)
    if q.first():
        raise HTTPException(409, f"{name} ending {last4} is already added.")


@router.get("", response_model=list[BankOut])
def list_banks(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Active banks for the receipt form; managers may include deactivated ones."""
    q = db.query(CompanyBank)
    if not (include_inactive and user.role in NON_EXEC):
        q = q.filter(CompanyBank.is_active.is_(True))
    return q.order_by(CompanyBank.name, CompanyBank.account_last4).all()


@router.post("", response_model=BankOut, status_code=201)
def create_bank(payload: BankCreate, db: Session = Depends(get_db), user: User = Depends(require_roles(*NON_EXEC))):
    _assert_new(db, payload.name, payload.account_last4)
    bank = CompanyBank(**payload.model_dump())
    db.add(bank)
    db.flush()
    log_action(db, user, "Added bank", "CompanyBank", bank.id, bank.label)
    db.commit()
    db.refresh(bank)
    return bank


@router.patch("/{bank_id}", response_model=BankOut)
def update_bank(
    bank_id: int, payload: BankUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles(*NON_EXEC))
):
    bank = _get(db, bank_id)
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    _assert_new(db, changes.get("name", bank.name), changes.get("account_last4", bank.account_last4), exclude_id=bank.id)
    for field, value in changes.items():
        setattr(bank, field, value)
    # Receipts already recorded keep the bank label they were saved with.
    log_action(db, user, "Updated bank", "CompanyBank", bank.id, ", ".join(sorted(changes)) or None)
    db.commit()
    db.refresh(bank)
    return bank


@router.delete("/{bank_id}", status_code=204)
def delete_bank(bank_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*NON_EXEC))):
    bank = _get(db, bank_id)
    used = db.query(Payment).filter(Payment.bank_account_id == bank.id).count()
    if used:
        raise HTTPException(409, f"{bank.label} has {used} receipt(s) recorded against it. Deactivate it instead.")
    db.delete(bank)
    log_action(db, user, "Deleted bank", "CompanyBank", bank_id, bank.label)
    db.commit()
