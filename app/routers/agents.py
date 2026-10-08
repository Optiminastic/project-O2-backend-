from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import PROFORMA_STAGES, Agent, Client, ClientInvoice, User, UserRole
from app.schemas.agent import AgentCreate, AgentOption, AgentUpdate, AgentOut
from app.services.agent_mou import render_mou_pdf
from app.services.agents import assert_house_agent_update, house_agent
from app.services.audit import log_action

router = APIRouter(prefix="/agents", tags=["agents"])

MANAGER_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)
FINANCE_ROLES = (*MANAGER_ROLES, UserRole.FINANCE_EXECUTIVE)


@router.get("", response_model=list[AgentOut])
def list_agents(
    search: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    q = db.query(Agent)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Agent.business_name.ilike(like), Agent.email.ilike(like)))
    return q.order_by(Agent.created_at.desc()).all()


@router.get("/options", response_model=list[AgentOption])
def agent_options(db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE_ROLES))):
    """Active agents for client and invoice forms, the in-house agent first."""
    return (
        db.query(Agent)
        .filter(Agent.is_active.is_(True))
        .order_by(Agent.is_house.desc(), Agent.business_name)
        .all()
    )


@router.post("", response_model=AgentOut, status_code=201)
def create_agent(
    payload: AgentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    agent = Agent(**payload.model_dump())
    db.add(agent)
    db.flush()
    log_action(db, user, "Created agent", "Agent", agent.id, agent.business_name)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGER_ROLES))):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@router.get("/{agent_id}/mou.pdf", response_class=Response)
def download_mou(agent_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGER_ROLES))):
    """The agent's MOU (commission terms) as a PDF download."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    pdf = render_mou_pdf(agent)
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in agent.business_name).strip("-") or "agent"
    log_action(db, user, "Downloaded agent MOU", "Agent", agent.id)
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="MOU-{safe_name}.pdf"'},
    )


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(
    agent_id: int,
    payload: AgentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    changes = payload.model_dump(exclude_unset=True)
    assert_house_agent_update(agent, changes)
    for k, v in changes.items():
        setattr(agent, k, v)
    log_action(db, user, "Updated agent", "Agent", agent.id)
    db.commit()
    db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=204)
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN_CEO, UserRole.CFO)),
):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.is_house:
        raise HTTPException(400, f"{agent.business_name} is the default in-house agent and cannot be deleted")
    issued = (
        db.query(ClientInvoice)
        .filter(ClientInvoice.agent_id == agent.id, ClientInvoice.status.notin_(PROFORMA_STAGES))
        .count()
    )
    if issued:
        raise HTTPException(
            409,
            f"{agent.business_name} is credited on {issued} issued tax invoice(s), which must keep their agent. "
            "Deactivate the agent instead.",
        )
    # Clients and not-yet-issued proformas move to the in-house agent (no commission).
    opti_id = house_agent(db).id
    moved_clients = db.query(Client).filter(Client.agent_id == agent.id).update({Client.agent_id: opti_id})
    moved_invoices = (
        db.query(ClientInvoice).filter(ClientInvoice.agent_id == agent.id).update({ClientInvoice.agent_id: opti_id})
    )
    db.delete(agent)
    log_action(
        db, user, "Deleted agent", "Agent", agent_id,
        f"{agent.business_name}; moved {moved_clients} client(s) and {moved_invoices} proforma(s) to the in-house agent",
    )
    db.commit()
