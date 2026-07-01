from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.security import verify_password, create_access_token, hash_password
from app.core.deps import get_current_user
from app.core.workspace import normalize_workspace_email
from app.models import User, UserRole, Invitation, InvitationStatus
from app.schemas.auth import (
    Token,
    UserOut,
    SignupRequest,
    SignupStatusOut,
    InvitePreviewOut,
    AcceptInviteRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password login. `username` is the user's email."""
    user = db.query(User).filter(User.email == form.username.lower()).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    token = create_access_token(subject=user.email, role=user.role.value, name=user.name)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ---------- Signup: the very first account becomes the CEO ----------
@router.get("/signup-available", response_model=SignupStatusOut)
def signup_available(db: Session = Depends(get_db)):
    """Direct signup is open only until the first account exists."""
    return SignupStatusOut(available=db.query(User).count() == 0)


@router.post("/signup", response_model=Token)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signup is closed. Ask your administrator for an invite.",
        )
    email = normalize_workspace_email(payload.email)
    user = User(
        name=payload.name.strip(),
        email=email,
        hashed_password=hash_password(payload.password),
        role=UserRole.ADMIN_CEO,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(subject=user.email, role=user.role.value, name=user.name)
    return Token(access_token=token)


# ---------- Invitations: public token lookup + accept ----------
def _invite_or_404(token: str, db: Session) -> Invitation:
    inv = db.query(Invitation).filter(Invitation.token == token).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found")
    return inv


def _invite_expired(inv: Invitation) -> bool:
    exp = inv.expires_at
    if exp.tzinfo is None:  # tolerate naive datetimes (e.g. SQLite)
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < datetime.now(timezone.utc)


@router.get("/invite/{token}", response_model=InvitePreviewOut)
def invite_preview(token: str, db: Session = Depends(get_db)):
    inv = _invite_or_404(token, db)
    valid = inv.status == InvitationStatus.PENDING and not _invite_expired(inv)
    return InvitePreviewOut(name=inv.name, email=inv.email, role=inv.role, valid=valid)


@router.post("/invite/{token}/accept", response_model=Token)
def accept_invite(token: str, payload: AcceptInviteRequest, db: Session = Depends(get_db)):
    inv = _invite_or_404(token, db)
    if inv.status != InvitationStatus.PENDING:
        raise HTTPException(status_code=400, detail="This invitation has already been used or revoked.")
    if _invite_expired(inv):
        raise HTTPException(status_code=400, detail="This invitation has expired. Ask for a new one.")
    if db.query(User).filter(User.email == inv.email).first():
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    user = User(
        name=(payload.name.strip() if payload.name and payload.name.strip() else inv.name),
        email=inv.email,
        hashed_password=hash_password(payload.password),
        role=inv.role,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    inv.status = InvitationStatus.ACCEPTED
    inv.accepted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token_str = create_access_token(subject=user.email, role=user.role.value, name=user.name)
    return Token(access_token=token_str)
