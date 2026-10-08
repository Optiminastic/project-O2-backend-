from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import (
    Vendor,
    VendorAllocation,
    VendorBankAccount,
    VendorInvoice,
    User,
    UserRole,
    VendorApprovalStatus,
)
from app.schemas.vendor import (
    VendorCreate,
    VendorUpdate,
    VendorOut,
    VendorBankAccountIn,
    AllocationCreate,
    AllocationOut,
    VendorInvoiceCreate,
    VendorInvoiceOut,
)
from app.services.taxation import TDS_SECTIONS, compute_gst, compute_tds, vendor_net_payable
from app.services.projects import assert_vendor_on_project
from app.services.audit import log_action
from app.core.period import Period, month_period

router = APIRouter(prefix="/vendors", tags=["vendors"])

MANAGER_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)

# Mandatory fields that must be present before a vendor can be verified.
# A vendor also needs at least one bank account to be paid into.
MANDATORY_VERIFY_FIELDS = {"gst_number": "GST number", "pan": "PAN"}

# Cannot be cleared on edit: a vendor always has a name and an email.
REQUIRED_FIELDS = ("business_name", "email")


def _bank_accounts(accounts: list[VendorBankAccountIn]) -> list[VendorBankAccount]:
    return [VendorBankAccount(**a.model_dump()) for a in accounts]


@router.get("", response_model=list[VendorOut])
def list_vendors(
    search: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Vendor)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Vendor.business_name.ilike(like), Vendor.email.ilike(like)))
    return q.order_by(Vendor.created_at.desc()).all()


@router.post("", response_model=VendorOut, status_code=201)
def create_vendor(
    payload: VendorCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES, UserRole.FINANCE_EXECUTIVE)),
):
    vendor = Vendor(**payload.model_dump(exclude={"bank_accounts"}))
    vendor.bank_accounts = _bank_accounts(payload.bank_accounts)
    db.add(vendor)
    db.flush()
    log_action(db, user, "Created vendor", "Vendor", vendor.id, vendor.business_name)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.get("/{vendor_id}", response_model=VendorOut)
def get_vendor(vendor_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    vendor = db.get(Vendor, vendor_id)
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    return vendor


@router.patch("/{vendor_id}", response_model=VendorOut)
def update_vendor(
    vendor_id: int,
    payload: VendorUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    vendor = db.get(Vendor, vendor_id)
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    changes = payload.model_dump(exclude_unset=True, exclude={"bank_accounts"})
    for k, v in changes.items():
        if v is None and k in REQUIRED_FIELDS:
            continue
        setattr(vendor, k, v)
    if payload.bank_accounts is not None:
        vendor.bank_accounts = _bank_accounts(payload.bank_accounts)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.post("/{vendor_id}/verify", response_model=VendorOut)
def verify_vendor(
    vendor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    """Mark vendor verified - only if all mandatory financial/tax/bank details exist."""
    vendor = db.get(Vendor, vendor_id)
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    missing = [label for field, label in MANDATORY_VERIFY_FIELDS.items() if not getattr(vendor, field)]
    if not vendor.bank_accounts:
        missing.append("a bank account")
    if missing:
        raise HTTPException(400, f"Cannot verify - missing mandatory details: {', '.join(missing)}")
    vendor.is_verified = True
    vendor.approval_status = VendorApprovalStatus.VERIFIED
    log_action(db, user, "Verified vendor onboarding", "Vendor", vendor.id)
    db.commit()
    db.refresh(vendor)
    return vendor


# ---------- Allocations ----------
@router.get("/allocations/all", response_model=list[AllocationOut], tags=["allocations"])
def list_allocations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(VendorAllocation).order_by(VendorAllocation.created_at.desc()).all()


@router.post("/allocations", response_model=AllocationOut, status_code=201, tags=["allocations"])
def create_allocation(
    payload: AllocationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    if not db.get(Vendor, payload.vendor_id):
        raise HTTPException(404, "Vendor not found")
    alloc = VendorAllocation(**payload.model_dump())
    db.add(alloc)
    db.flush()
    log_action(db, user, "Allocated vendor to project", "VendorAllocation", alloc.id, alloc.project_name)
    db.commit()
    db.refresh(alloc)
    return alloc


# ---------- Vendor invoices ----------
@router.get("/invoices/all", response_model=list[VendorInvoiceOut], tags=["vendor-invoices"])
def list_vendor_invoices(
    period: Period | None = Depends(month_period),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(VendorInvoice)
    if period:
        q = q.filter(period.dates(VendorInvoice.invoice_date))
    return q.order_by(VendorInvoice.created_at.desc()).all()


@router.post("/invoices", response_model=VendorInvoiceOut, status_code=201, tags=["vendor-invoices"])
def create_vendor_invoice(
    payload: VendorInvoiceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES, UserRole.FINANCE_EXECUTIVE)),
):
    vendor = db.get(Vendor, payload.vendor_id)
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    if payload.project_id is not None:
        assert_vendor_on_project(db, payload.project_id, vendor.id)
    gst = compute_gst(payload.invoice_amount, payload.gst_rate, payload.is_interstate)
    section = TDS_SECTIONS[payload.tds_section]
    inv = VendorInvoice(
        **payload.model_dump(),
        gst_amount=gst.gst_amount,
        cgst=gst.cgst,
        sgst=gst.sgst,
        igst=gst.igst,
        vendor_gstin=vendor.gst_number,
        tds_applicable=section.rate > 0,
        tds_rate=section.rate,
    )
    inv.tds_amount = compute_tds(inv.invoice_amount, inv.tds_rate, inv.tds_applicable)
    inv.net_payable = vendor_net_payable(inv.invoice_amount, inv.gst_amount, inv.tds_amount)
    db.add(inv)
    db.flush()
    log_action(db, user, "Recorded vendor invoice", "VendorInvoice", inv.id, inv.invoice_number)
    db.commit()
    db.refresh(inv)
    return inv
