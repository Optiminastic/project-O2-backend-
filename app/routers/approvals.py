from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import require_roles
from app.core.rbac import NON_EXEC
from app.models import PaymentApproval, User, UserRole, ApprovalStatus
from app.schemas.misc import ApprovalCreate, ApprovalOut, ApprovalDetail, ApprovalDecision, PaymentRelease
from app.services import approval as approval_svc
from app.services.audit import log_action
from app.core.period import Period, month_period

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    status: ApprovalStatus | None = None,
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    q = db.query(PaymentApproval)
    if period:
        q = q.filter(period.timestamps(PaymentApproval.created_at))
    if status:
        q = q.filter(PaymentApproval.status == status)
    return q.order_by(PaymentApproval.created_at.desc()).all()


@router.post("", response_model=ApprovalDetail, status_code=201)
def create_approval(
    payload: ApprovalCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*NON_EXEC)),
):
    approval = PaymentApproval(**payload.model_dump(), requested_by_id=user.id)
    if not approval.net_payable:
        approval.net_payable = approval.amount - approval.tax_deductions
    db.add(approval)
    db.flush()
    log_action(db, user, "Created payment approval request", "PaymentApproval", approval.id, approval.payee_name)
    db.commit()
    db.refresh(approval)
    return approval


@router.get("/{approval_id}", response_model=ApprovalDetail)
def get_approval(approval_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*NON_EXEC))):
    approval = db.get(PaymentApproval, approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    return approval


def _get(db: Session, approval_id: int) -> PaymentApproval:
    approval = db.get(PaymentApproval, approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    return approval


@router.post("/{approval_id}/submit", response_model=ApprovalDetail)
def submit(approval_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*NON_EXEC))):
    approval = _get(db, approval_id)
    approval_svc.submit_for_approval(db, approval, user)
    db.commit()
    db.refresh(approval)
    return approval


@router.post("/{approval_id}/ceo", response_model=ApprovalDetail)
def ceo_decision(
    approval_id: int,
    decision: ApprovalDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN_CEO)),
):
    approval = _get(db, approval_id)
    approval_svc.ceo_decision(db, approval, user, decision.approve, decision.comment)
    db.commit()
    db.refresh(approval)
    return approval


@router.post("/{approval_id}/release", response_model=ApprovalDetail)
def release(
    approval_id: int,
    payload: PaymentRelease,
    db: Session = Depends(get_db),
    # The Finance Manager records the payout's reference ID to complete it.
    user: User = Depends(require_roles(UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)),
):
    approval = _get(db, approval_id)
    approval_svc.release_payment(db, approval, user, payload.payment_reference)
    db.commit()
    db.refresh(approval)
    return approval
