"""Agent rules: every client and invoice is credited to an agent.

Clients who came in directly belong to the in-house agent "Opti", which never
earns commission and can be neither deactivated nor deleted.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Agent

HOUSE_AGENT_NAME = "Opti"
HOUSE_AGENT_LEGAL_NAME = "Optiminastic (in-house, no commission)"


def ensure_house_agent(db: Session) -> Agent:
    """Create the house agent if it is missing. Idempotent; the caller commits."""
    agent = db.query(Agent).filter(Agent.is_house.is_(True)).first()
    if agent:
        return agent
    agent = Agent(
        business_name=HOUSE_AGENT_NAME,
        legal_name=HOUSE_AGENT_LEGAL_NAME,
        email=f"finance@{settings.workspace_email_domain}",
        commission_rate=0.0,
        is_active=True,
        is_house=True,
        notes="Default agent for clients who came in directly. Earns no commission.",
    )
    db.add(agent)
    db.flush()
    return agent


def house_agent(db: Session) -> Agent:
    agent = db.query(Agent).filter(Agent.is_house.is_(True)).first()
    if agent is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "The in-house agent is missing; restart the API")
    return agent


def resolve_agent_id(db: Session, agent_id: int | None) -> int:
    """The agent to credit: the one given, or Opti when none was chosen."""
    if agent_id is None:
        return house_agent(db).id
    if not db.get(Agent, agent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent_id


def assert_house_agent_update(agent: Agent, changes: dict) -> None:
    if not agent.is_house:
        return
    if changes.get("commission_rate") not in (None, 0, 0.0):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{HOUSE_AGENT_NAME} is in-house and never earns commission")
    if changes.get("is_active") is False:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{HOUSE_AGENT_NAME} is the default agent and cannot be deactivated")
