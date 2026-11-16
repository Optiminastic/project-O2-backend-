import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models import (
    ProjectVendor,
    ProjectVendorService,
    Vendor,
    VendorAllocation,
    VendorBankAccount,
    VendorInvoice,
    VendorInvoiceLine,
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
    VendorServicesIn,
)
from app.schemas.project import VendorAssignmentOut
from app.schemas.misc import PayableLineOut, VendorPayableOut
from app.services import storage
from app.services.vendor_payouts import bill_dues
from app.services.taxation import TDS_SECTIONS, compute_gst, compute_tds, vendor_net_payable
from app.services.projects import billable_assignments, vendor_assignments
from app.services.service_catalog import usable_services
from app.services.audit import log_action
from app.services.group_companies import assert_may_manage, assert_visible, sees_group_companies, visible_vendors
from app.core.period import Period, month_period

router = APIRouter(prefix="/vendors", tags=["vendors"])

MANAGER_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)

# Mandatory fields that must be present before a vendor can be verified.
# A vendor also needs at least one bank account to be paid into.
MANDATORY_VERIFY_FIELDS = {"gst_number": "GST number", "pan": "PAN"}

# Cannot be cleared on edit: an explicit null for one of these is ignored.
REQUIRED_FIELDS = ("business_name", "email", "gst_number", "address", "is_group_company", "commission_rate")

# A vendor's invoice document: PDF or an image, up to 10 MB.
DOCUMENT_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def _bank_accounts(accounts: list[VendorBankAccountIn]) -> list[VendorBankAccount]:
    return [VendorBankAccount(**a.model_dump()) for a in accounts]


@router.get("", response_model=list[VendorOut])
def list_vendors(
    search: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = visible_vendors(db.query(Vendor), user)
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
    assert_may_manage(user, payload.is_group_company)
    vendor = Vendor(**payload.model_dump(exclude={"bank_accounts", "service_ids"}))
    vendor.bank_accounts = _bank_accounts(payload.bank_accounts)
    if payload.service_ids:
        vendor.services = usable_services(db, payload.service_ids)
    db.add(vendor)
    db.flush()
    log_action(db, user, "Created vendor", "Vendor", vendor.id, vendor.business_name)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.get("/{vendor_id}", response_model=VendorOut)
def get_vendor(vendor_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return assert_visible(db.get(Vendor, vendor_id), user)


@router.patch("/{vendor_id}", response_model=VendorOut)
def update_vendor(
    vendor_id: int,
    payload: VendorUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    vendor = assert_visible(db.get(Vendor, vendor_id), user)
    assert_may_manage(user, vendor.is_group_company or bool(payload.is_group_company))
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
    vendor = assert_visible(db.get(Vendor, vendor_id), user)
    assert_may_manage(user, vendor.is_group_company)
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


# ---------- Services the vendor provides ----------

@router.put("/{vendor_id}/services", response_model=VendorOut)
def set_vendor_services(
    vendor_id: int,
    payload: VendorServicesIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    """Replace the services the vendor provides.

    A service the vendor is assigned on a project stays on the list until that
    assignment is removed.
    """
    vendor = assert_visible(db.get(Vendor, vendor_id), user)
    assert_may_manage(user, vendor.is_group_company)
    wanted = list(dict.fromkeys(payload.service_ids))
    kept = {s.id: s for s in vendor.services if s.id in wanted}
    # A service already on the list stays usable even if since deactivated; new ones must be active.
    added = usable_services(db, [sid for sid in wanted if sid not in kept]) if len(kept) < len(wanted) else []
    by_id = {**kept, **{s.id: s for s in added}}

    in_use = (
        db.query(ProjectVendorService)
        .join(ProjectVendor, ProjectVendor.id == ProjectVendorService.project_vendor_id)
        .filter(ProjectVendor.vendor_id == vendor.id, ProjectVendorService.service_id.notin_(wanted or [-1]))
        .all()
    )
    if in_use:
        listed = ", ".join(sorted({f"{a.service.title} ({a.code})" for a in in_use}))
        raise HTTPException(
            400, f"{vendor.business_name} is assigned {listed} on a project. Remove that assignment first."
        )
    vendor.services = [by_id[sid] for sid in wanted]
    log_action(db, user, "Updated vendor services", "Vendor", vendor.id,
               ", ".join(s.title for s in vendor.services) or "none")
    db.commit()
    db.refresh(vendor)
    return vendor


@router.get("/{vendor_id}/payables", response_model=list[VendorPayableOut])
def list_vendor_payables(vendor_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The vendor's bills with what is still owed on each, oldest first: what an outward payment can pay."""
    vendor = assert_visible(db.get(Vendor, vendor_id), user)
    invoices = (
        db.query(VendorInvoice).filter(VendorInvoice.vendor_id == vendor.id)
        .order_by(VendorInvoice.invoice_date, VendorInvoice.id).all()
    )
    return [
        VendorPayableOut(
            invoice_id=b.invoice.id, invoice_number=b.invoice.invoice_number, invoice_date=b.invoice.invoice_date,
            project_id=b.invoice.project_id, status=b.invoice.status.value, amount_payable=b.payable,
            committed=b.committed, paid=b.paid, due=b.due,
            lines=[PayableLineOut(line_id=line.line_id, code=line.code, service_title=line.service_title,
                                  payable=line.payable, committed=line.committed, paid=line.paid, due=line.due)
                   for line in b.lines],
        )
        for b in bill_dues(db, invoices)
    ]


@router.get("/{vendor_id}/assignments", response_model=list[VendorAssignmentOut])
def list_vendor_assignments(vendor_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The project service codes the vendor can bill against, with what is billed so far."""
    vendor = assert_visible(db.get(Vendor, vendor_id), user)
    return vendor_assignments(db, vendor.id)


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
    if not sees_group_companies(user):
        q = q.join(Vendor, Vendor.id == VendorInvoice.vendor_id).filter(Vendor.is_group_company.is_(False))
    if period:
        q = q.filter(period.dates(VendorInvoice.invoice_date))
    return q.order_by(VendorInvoice.created_at.desc()).all()


@router.post("/invoices", response_model=VendorInvoiceOut, status_code=201, tags=["vendor-invoices"])
def create_vendor_invoice(
    payload: VendorInvoiceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES, UserRole.FINANCE_EXECUTIVE)),
):
    vendor = assert_visible(db.get(Vendor, payload.vendor_id), user)
    assignments = billable_assignments(db, vendor.id, [line.assignment_id for line in payload.lines])
    amount = round(sum(line.amount for line in payload.lines), 2)
    gst = compute_gst(amount, payload.gst_rate, payload.is_interstate)
    section = TDS_SECTIONS[payload.tds_section]
    inv = VendorInvoice(
        **payload.model_dump(exclude={"lines"}),
        project_id=assignments[0].project_vendor.project_id,
        invoice_amount=amount,
        lines=[VendorInvoiceLine(assignment=a, amount=line.amount) for a, line in zip(assignments, payload.lines)],
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
    # Optiminastic's commission is a share of the bill before GST, kept back from the payout.
    inv.commission_rate = vendor.commission_rate if payload.commission_rate is None else payload.commission_rate
    inv.commission_amount = round(inv.invoice_amount * inv.commission_rate / 100, 2)
    if inv.commission_amount > inv.net_payable:
        raise HTTPException(400, "The commission is more than the vendor's net payable on this bill.")
    inv.amount_payable = round(inv.net_payable - inv.commission_amount, 2)
    db.add(inv)
    db.flush()
    log_action(db, user, "Recorded vendor invoice", "VendorInvoice", inv.id, inv.invoice_number)
    db.commit()
    db.refresh(inv)
    return inv


# ---------- The vendor's invoice document ----------

def _bill(db: Session, invoice_id: int, user: User) -> VendorInvoice:
    inv = db.get(VendorInvoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Vendor invoice not found")
    assert_visible(inv.vendor, user)
    return inv


@router.post("/invoices/{invoice_id}/document", response_model=VendorInvoiceOut, tags=["vendor-invoices"])
async def upload_vendor_invoice_document(
    invoice_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGER_ROLES, UserRole.FINANCE_EXECUTIVE)),
):
    """Attach (or replace) the vendor's own invoice: a PDF or an image."""
    inv = _bill(db, invoice_id, user)
    if file.content_type not in DOCUMENT_TYPES:
        raise HTTPException(400, "Upload the invoice as a PDF or an image (PNG, JPG or WebP).")
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(400, "The file is larger than 10 MB.")
    if not content:
        raise HTTPException(400, "The file is empty.")
    name = os.path.basename(file.filename or "invoice")[:200]
    inv.document_path = storage.save(content, name, file.content_type)
    inv.document_name = name
    log_action(db, user, "Uploaded vendor invoice document", "VendorInvoice", inv.id, name)
    db.commit()
    db.refresh(inv)
    return inv


@router.get("/invoices/{invoice_id}/document", tags=["vendor-invoices"])
def download_vendor_invoice_document(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    inv = _bill(db, invoice_id, user)
    if not inv.document_path:
        raise HTTPException(404, "No document uploaded for this invoice")
    url = storage.resolve_url(inv.document_path)
    if url:
        return RedirectResponse(url)
    if not os.path.exists(inv.document_path):
        raise HTTPException(404, "The document file is missing")
    return FileResponse(inv.document_path, filename=inv.document_name or "invoice")
