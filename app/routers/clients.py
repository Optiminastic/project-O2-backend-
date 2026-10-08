from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import Client, User, UserRole
from app.schemas.client import ClientCreate, ClientUpdate, ClientOut
from app.services.audit import log_action
from app.services.agents import resolve_agent_id

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=list[ClientOut])
def list_clients(
    search: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Client)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Client.business_name.ilike(like), Client.email.ilike(like)))
    return q.order_by(Client.created_at.desc()).all()


@router.post("", response_model=ClientOut, status_code=201)
def create_client(
    payload: ClientCreate,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_roles(UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER, UserRole.FINANCE_EXECUTIVE)
    ),
):
    data = payload.model_dump()
    data["agent_id"] = resolve_agent_id(db, data.get("agent_id"))
    client = Client(**data)
    db.add(client)
    db.flush()
    log_action(db, user, "Created client", "Client", client.id, client.business_name)
    db.commit()
    db.refresh(client)
    return client


@router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    return client


@router.patch("/{client_id}", response_model=ClientOut)
def update_client(
    client_id: int,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)),
):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    changes = payload.model_dump(exclude_unset=True)
    if "agent_id" in changes:
        changes["agent_id"] = resolve_agent_id(db, changes["agent_id"])
    for k, v in changes.items():
        setattr(client, k, v)
    log_action(db, user, "Updated client", "Client", client.id)
    db.commit()
    db.refresh(client)
    return client


@router.delete("/{client_id}", status_code=204)
def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN_CEO, UserRole.CFO)),
):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    db.delete(client)
    log_action(db, user, "Deleted client", "Client", client_id)
    db.commit()
