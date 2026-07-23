import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import (
    BankStatement,
    BankTransaction,
    User,
    UserRole,
    VerificationStatus,
)
from app.schemas.misc import BankStatementOut, BankStatementDetail, BankTransactionOut
from app.services.reconciliation import parse_statement, auto_match
from app.services import storage
from app.services.audit import log_action

router = APIRouter(prefix="/verification", tags=["verification"])

MANAGER_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)


@router.get("/statements", response_model=list[BankStatementOut])
def list_statements(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(BankStatement).order_by(BankStatement.created_at.desc()).all()


@router.get("/statements/{statement_id}", response_model=BankStatementDetail)
def get_statement(statement_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = db.get(BankStatement, statement_id)
    if not stmt:
        raise HTTPException(404, "Statement not found")
    return stmt


@router.get("/statements/{statement_id}/download")
def download_statement(
    statement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Download the original uploaded statement file.

    For S3-backed storage this redirects to a short-lived presigned URL; for
    local storage the file is streamed directly.
    """
    stmt = db.get(BankStatement, statement_id)
    if not stmt or not stmt.file_path:
        raise HTTPException(404, "Statement file not found")

    link = storage.resolve_url(stmt.file_path)
    if link:
        return RedirectResponse(link)
    if os.path.exists(stmt.file_path):
        return FileResponse(stmt.file_path, filename=stmt.file_name)
    raise HTTPException(404, "Statement file is no longer available")


@router.post("/statements", response_model=BankStatementDetail, status_code=201)
async def upload_statement(
    file: UploadFile = File(...),
    bank_name: str | None = Form(None),
    account_number: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES, UserRole.FINANCE_EXECUTIVE)),
):
    """Upload a bank statement (CSV/Excel/PDF), parse it and auto-match transactions."""
    content = await file.read()

    # Persist the original file (S3 when configured, else local disk) and keep the
    # returned reference on the record for later download.
    reference = storage.save(content, file.filename or "statement", file.content_type)

    stmt = BankStatement(
        file_name=file.filename or reference,
        file_path=reference,
        bank_name=bank_name,
        account_number=account_number,
        uploaded_by=user.name,
    )
    db.add(stmt)
    db.flush()

    rows = parse_statement(content, file.filename or "")
    matched = 0
    for row in rows:
        txn = BankTransaction(statement_id=stmt.id, **row)
        db.add(txn)
        db.flush()
        if auto_match(db, txn):
            matched += 1

    stmt.transaction_count = len(rows)
    stmt.matched_count = matched
    log_action(
        db, user, "Uploaded bank statement", "BankStatement", stmt.id,
        f"{len(rows)} txns · {matched} auto-matched",
    )
    db.commit()
    db.refresh(stmt)
    return stmt


@router.post("/transactions/{txn_id}/match", response_model=BankTransactionOut)
def manual_match(
    txn_id: int,
    payment_id: int | None = None,
    approval_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    txn = db.get(BankTransaction, txn_id)
    if not txn:
        raise HTTPException(404, "Transaction not found")
    txn.matched_payment_id = payment_id
    txn.matched_approval_id = approval_id
    txn.verification_status = VerificationStatus.MANUALLY_MATCHED
    txn.match_note = "Manually matched by " + user.name
    log_action(db, user, "Manually matched transaction", "BankTransaction", txn.id)
    db.commit()
    db.refresh(txn)
    return txn


@router.post("/transactions/{txn_id}/reconcile", response_model=BankTransactionOut)
def reconcile(
    txn_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    txn = db.get(BankTransaction, txn_id)
    if not txn:
        raise HTTPException(404, "Transaction not found")
    if txn.verification_status not in {VerificationStatus.AUTO_MATCHED, VerificationStatus.MANUALLY_MATCHED}:
        raise HTTPException(400, "Only matched transactions can be reconciled")
    txn.verification_status = VerificationStatus.RECONCILED
    log_action(db, user, "Reconciled transaction", "BankTransaction", txn.id)
    db.commit()
    db.refresh(txn)
    return txn
