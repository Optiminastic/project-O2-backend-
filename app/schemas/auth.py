from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict, Field

from app.models.enums import UserRole, InvitationStatus


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    role: UserRole
    is_active: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ---------- Signup (first user becomes the CEO) ----------
class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class SignupStatusOut(BaseModel):
    """Whether direct signup is still open (true only while no users exist)."""

    available: bool


# ---------- Team invitations (the 'circle') ----------
class InviteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    role: UserRole = UserRole.FINANCE_EXECUTIVE


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    role: UserRole
    status: InvitationStatus
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime
    # Surfaced to the inviter so they can copy/share the link if email didn't send.
    accept_url: str | None = None
    email_sent: bool | None = None


class InvitePreviewOut(BaseModel):
    """Public, unauthenticated view of an invite for the accept page."""

    name: str
    email: EmailStr
    role: UserRole
    valid: bool


class AcceptInviteRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=120)


class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime
