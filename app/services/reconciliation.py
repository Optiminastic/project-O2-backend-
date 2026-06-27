"""Bank statement parsing + payment matching."""

import io
from datetime import date

import pandas as pd

from sqlalchemy.orm import Session

from app.models import BankTransaction, Payment, PaymentApproval, VerificationStatus, ApprovalStatus


def _pick(cols: list[str], *needles: str) -> str | None:
    for c in cols:
        lc = c.lower()
        if any(n in lc for n in needles):
            return c
    return None


def parse_statement(content: bytes, filename: str) -> list[dict]:
    """Parse a CSV/Excel bank statement into normalized transaction dicts.

    PDF statements are stored but not auto-parsed (returns an empty list).
    """
    name = filename.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content))
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(content))
    else:
        return []  # PDF / unknown — stored only

    df.columns = [str(c).strip() for c in df.columns]
    cols = list(df.columns)
    date_col = _pick(cols, "date", "txn date", "value date")
    amount_col = _pick(cols, "amount", "credit", "debit")
    utr_col = _pick(cols, "utr", "ref", "reference", "cheque")
    narration_col = _pick(cols, "narration", "description", "particular", "remark")
    party_col = _pick(cols, "counterparty", "payee", "beneficiary", "name")

    rows: list[dict] = []
    for _, r in df.iterrows():
        amount_raw = r.get(amount_col) if amount_col else 0
        try:
            amount = abs(float(str(amount_raw).replace(",", "").strip() or 0))
        except (ValueError, TypeError):
            amount = 0.0

        txn_date = None
        if date_col and pd.notna(r.get(date_col)):
            try:
                txn_date = pd.to_datetime(r.get(date_col), dayfirst=True).date()
            except (ValueError, TypeError):
                txn_date = None

        rows.append(
            {
                "txn_date": txn_date,
                "amount": amount,
                "utr_reference": str(r.get(utr_col)).strip() if utr_col and pd.notna(r.get(utr_col)) else None,
                "narration": str(r.get(narration_col)).strip() if narration_col and pd.notna(r.get(narration_col)) else None,
                "counterparty": str(r.get(party_col)).strip() if party_col and pd.notna(r.get(party_col)) else None,
            }
        )
    return rows


def auto_match(db: Session, txn: BankTransaction) -> bool:
    """Try to match a bank transaction to a payment or released approval.

    Returns True if matched. Matching is by UTR reference, then by amount (+date).
    """
    # 1) Match against client invoice payments by bank reference, then amount.
    payment = None
    if txn.utr_reference:
        payment = (
            db.query(Payment)
            .filter(Payment.bank_reference == txn.utr_reference)
            .first()
        )
    if payment is None and txn.amount > 0:
        payment = (
            db.query(Payment)
            .filter(Payment.amount == txn.amount)
            .first()
        )
    if payment is not None:
        txn.matched_payment_id = payment.id
        txn.verification_status = VerificationStatus.AUTO_MATCHED
        txn.match_note = f"Matched payment #{payment.id} on invoice #{payment.invoice_id}"
        return True

    # 2) Match against released payment approvals by amount.
    approval = (
        db.query(PaymentApproval)
        .filter(
            PaymentApproval.net_payable == txn.amount,
            PaymentApproval.status.in_(
                [ApprovalStatus.PAYMENT_RELEASED, ApprovalStatus.PAYMENT_READY]
            ),
        )
        .first()
    )
    if approval is not None:
        txn.matched_approval_id = approval.id
        txn.verification_status = VerificationStatus.AUTO_MATCHED
        txn.match_note = f"Matched approval #{approval.id} ({approval.payee_name})"
        return True

    txn.verification_status = VerificationStatus.MISMATCH
    txn.match_note = "No matching system record found"
    return False
