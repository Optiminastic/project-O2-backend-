from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import VendorReport, EmailLog, Client, User, UserRole, ReportReviewStatus
from app.schemas.misc import ReportCreate, ReportOut, EmailReportRequest, EmailLogOut
from app.services.audit import log_action

router = APIRouter(prefix="/reports", tags=["reports"])

MANAGER_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)


@router.get("", response_model=list[ReportOut])
def list_reports(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(VendorReport).order_by(VendorReport.created_at.desc()).all()


@router.post("", response_model=ReportOut, status_code=201)
def create_report(
    payload: ReportCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES, UserRole.FINANCE_EXECUTIVE)),
):
    report = VendorReport(**payload.model_dump())
    if report.submission_date is None:
        report.submission_date = date.today()
    db.add(report)
    db.flush()
    log_action(db, user, "Uploaded vendor report", "VendorReport", report.id, report.project_name)
    db.commit()
    db.refresh(report)
    return report


@router.post("/{report_id}/review", response_model=ReportOut)
def review_report(
    report_id: int,
    approve: bool,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    report = db.get(VendorReport, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    report.review_status = ReportReviewStatus.APPROVED if approve else ReportReviewStatus.REJECTED
    report.internal_reviewer = user.name
    log_action(db, user, f"Report {'approved' if approve else 'rejected'}", "VendorReport", report.id)
    db.commit()
    db.refresh(report)
    return report


@router.post("/{report_id}/email", response_model=EmailLogOut, status_code=201)
def email_report(
    report_id: int,
    payload: EmailReportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    report = db.get(VendorReport, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if report.review_status not in {ReportReviewStatus.APPROVED, ReportReviewStatus.SENT_TO_CLIENT}:
        raise HTTPException(400, "Report must be approved before emailing to client")

    to_email = payload.to_email
    if not to_email and report.client_id:
        client = db.get(Client, report.client_id)
        to_email = client.email if client else None
    if not to_email:
        raise HTTPException(400, "No recipient email — provide one or link a client")

    subject = payload.subject or f"{report.project_name} — {report.reporting_period or 'Report'}"
    body = payload.body or f"Please find attached the {report.report_type or 'report'} for {report.project_name}."

    # Email delivery is simulated (SMTP not wired in dev) but fully logged.
    log = EmailLog(
        report_id=report.id,
        to_email=to_email,
        subject=subject,
        body=body,
        delivery_status="Sent (simulated)",
        sent_by=user.name,
    )
    report.review_status = ReportReviewStatus.SENT_TO_CLIENT
    db.add(log)
    db.flush()
    log_action(db, user, "Emailed report to client", "VendorReport", report.id, to_email)
    db.commit()
    db.refresh(log)
    return log
