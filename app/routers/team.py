"""The 'circle' — team membership and invitations, managed by the CEO."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.core.deps import require_ceo
from app.core.workspace import normalize_workspace_email
from app.models import User, UserRole, Invitation, InvitationStatus
from app.schemas.auth import InviteCreate, InviteOut, TeamMemberOut
from app.services.email import send_invite_email

router = APIRouter(prefix="/team", tags=["team"])

ROLE_LABELS = {
    UserRole.ADMIN_CEO: "Admin / CEO",
    UserRole.CFO: "CFO",
    UserRole.FINANCE_MANAGER: "Finance Manager",
    UserRole.FINANCE_EXECUTIVE: "Finance Executive",
}


def _accept_url(token: str) -> str:
    return f"{settings.frontend_origin.rstrip('/')}/portal/accept?token={token}"


def _to_out(inv: Invitation, *, include_url: bool, email_sent: bool | None = None) -> InviteOut:
    return InviteOut(
        id=inv.id,
        name=inv.name,
        email=inv.email,
        role=inv.role,
        status=inv.status,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        created_at=inv.created_at,
        accept_url=_accept_url(inv.token) if include_url else None,
        email_sent=email_sent,
    )


def _send(inv: Invitation, inviter: User) -> bool:
    return send_invite_email(
        to_email=inv.email,
        to_name=inv.name,
        accept_url=_accept_url(inv.token),
        inviter_name=inviter.name,
        role_label=ROLE_LABELS.get(inv.role, inv.role.value),
    )


@router.get("/members", response_model=list[TeamMemberOut])
def members(db: Session = Depends(get_db), _: User = Depends(require_ceo)):
    return db.query(User).order_by(User.created_at.asc()).all()


@router.get("/invitations", response_model=list[InviteOut])
def list_invitations(db: Session = Depends(get_db), _: User = Depends(require_ceo)):
    rows = db.query(Invitation).order_by(Invitation.created_at.desc()).all()
    return [_to_out(i, include_url=(i.status == InvitationStatus.PENDING)) for i in rows]


@router.post("/invitations", response_model=InviteOut)
def create_invitation(
    payload: InviteCreate, db: Session = Depends(get_db), ceo: User = Depends(require_ceo)
):
    email = normalize_workspace_email(payload.email)
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="A member with this email already exists.")

    # Supersede any earlier pending invite to the same address.
    for stale in (
        db.query(Invitation)
        .filter(Invitation.email == email, Invitation.status == InvitationStatus.PENDING)
        .all()
    ):
        stale.status = InvitationStatus.REVOKED

    now = datetime.now(timezone.utc)
    inv = Invitation(
        email=email,
        name=payload.name.strip(),
        role=payload.role,
        token=secrets.token_urlsafe(32),
        status=InvitationStatus.PENDING,
        invited_by_id=ceo.id,
        expires_at=now + timedelta(hours=settings.invite_expire_hours),
        last_sent_at=now,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    sent = _send(inv, ceo)
    return _to_out(inv, include_url=True, email_sent=sent)


@router.post("/invitations/{invite_id}/resend", response_model=InviteOut)
def resend_invitation(
    invite_id: int, db: Session = Depends(get_db), ceo: User = Depends(require_ceo)
):
    inv = db.get(Invitation, invite_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if inv.status != InvitationStatus.PENDING:
        raise HTTPException(status_code=400, detail="Only pending invitations can be resent.")

    now = datetime.now(timezone.utc)
    inv.expires_at = now + timedelta(hours=settings.invite_expire_hours)
    inv.last_sent_at = now
    db.commit()
    db.refresh(inv)

    sent = _send(inv, ceo)
    return _to_out(inv, include_url=True, email_sent=sent)


@router.post("/invitations/{invite_id}/revoke", response_model=InviteOut)
def revoke_invitation(
    invite_id: int, db: Session = Depends(get_db), _: User = Depends(require_ceo)
):
    inv = db.get(Invitation, invite_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if inv.status == InvitationStatus.PENDING:
        inv.status = InvitationStatus.REVOKED
        db.commit()
        db.refresh(inv)
    return _to_out(inv, include_url=False)
