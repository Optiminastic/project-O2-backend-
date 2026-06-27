"""CFO -> CEO payment approval state machine."""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import PaymentApproval, ApprovalAction, ApprovalStatus, User, UserRole


# Allowed transitions: status -> {action: next_status}
def _record(db: Session, approval: PaymentApproval, user: User, decision: str, comments: str | None):
    db.add(
        ApprovalAction(
            approval_id=approval.id,
            approver_name=user.name,
            approver_role=user.role.value,
            decision=decision,
            comments=comments,
        )
    )


def submit_for_cfo(db: Session, approval: PaymentApproval, user: User) -> None:
    if approval.status not in {ApprovalStatus.DRAFT, ApprovalStatus.CFO_REJECTED, ApprovalStatus.CEO_REJECTED}:
        raise HTTPException(400, f"Cannot submit from status '{approval.status.value}'")
    approval.status = ApprovalStatus.SUBMITTED_CFO
    _record(db, approval, user, "Submitted for CFO Approval", None)


def cfo_decision(db: Session, approval: PaymentApproval, user: User, approve: bool, comment: str | None) -> None:
    if user.role != UserRole.CFO:
        raise HTTPException(403, "Only the CFO can take this action")
    if approval.status != ApprovalStatus.SUBMITTED_CFO:
        raise HTTPException(400, "Approval is not awaiting CFO decision")
    approval.cfo_comment = comment
    if approve:
        approval.status = ApprovalStatus.CFO_APPROVED
        _record(db, approval, user, "CFO Approved", comment)
        # Auto-advance to await CEO.
        approval.status = ApprovalStatus.SUBMITTED_CEO
        _record(db, approval, user, "Submitted for CEO Approval", None)
    else:
        approval.status = ApprovalStatus.CFO_REJECTED
        _record(db, approval, user, "CFO Rejected", comment)


def ceo_decision(db: Session, approval: PaymentApproval, user: User, approve: bool, comment: str | None) -> None:
    if user.role != UserRole.ADMIN_CEO:
        raise HTTPException(403, "Only the CEO can take this action")
    if approval.status != ApprovalStatus.SUBMITTED_CEO:
        raise HTTPException(400, "Approval is not awaiting CEO decision")
    approval.ceo_comment = comment
    if approve:
        approval.status = ApprovalStatus.CEO_APPROVED
        _record(db, approval, user, "CEO Approved", comment)
        # Both approvals complete -> payment is ready for release.
        approval.status = ApprovalStatus.PAYMENT_READY
        _record(db, approval, user, "Payment Ready", None)
    else:
        approval.status = ApprovalStatus.CEO_REJECTED
        _record(db, approval, user, "CEO Rejected", comment)


def release_payment(db: Session, approval: PaymentApproval, user: User) -> None:
    if approval.status != ApprovalStatus.PAYMENT_READY:
        raise HTTPException(400, "Payment is not ready for release (requires CFO + CEO approval)")
    approval.status = ApprovalStatus.PAYMENT_RELEASED
    approval.released_at = datetime.now(timezone.utc)
    _record(db, approval, user, "Payment Released", None)


def mark_verified(db: Session, approval: PaymentApproval, user: User) -> None:
    if approval.status != ApprovalStatus.PAYMENT_RELEASED:
        raise HTTPException(400, "Only a released payment can be verified")
    approval.status = ApprovalStatus.PAYMENT_VERIFIED
    _record(db, approval, user, "Payment Verified", None)
