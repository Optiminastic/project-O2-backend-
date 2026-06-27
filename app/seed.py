"""Seed Project O2 with demo users and representative data across every module.

Run:  python -m app.seed
Idempotent: it clears existing data first, then reseeds.
"""

from datetime import date, timedelta

from app.database import Base, engine, SessionLocal
import app.models  # noqa: F401
from app.core.security import hash_password
from app.models import (
    User,
    UserRole,
    Client,
    ClientInvoice,
    Payment,
    Vendor,
    VendorAllocation,
    VendorInvoice,
    VendorReport,
    PaymentApproval,
    BankStatement,
    BankTransaction,
    InvoiceStatus,
    GstStatus,
    PaymentMode,
    VendorApprovalStatus,
    AllocationStatus,
    ReportReviewStatus,
    ApprovalStatus,
    VerificationStatus,
)
from app.services.taxation import compute_gst, compute_tds, vendor_net_payable
from app.services.invoice_lock import recompute_invoice

DEMO_PASSWORD = "Password123!"

USERS = [
    ("Aarav Khanna", "ceo@optiminastic.com", UserRole.ADMIN_CEO),
    ("Lena Okafor", "cfo@optiminastic.com", UserRole.CFO),
    ("Daniel Roy", "manager@optiminastic.com", UserRole.FINANCE_MANAGER),
    ("Priya Nair", "exec@optiminastic.com", UserRole.FINANCE_EXECUTIVE),
]


def reset() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed() -> None:
    reset()
    db = SessionLocal()
    try:
        # Users
        users = {}
        for name, email, role in USERS:
            u = User(name=name, email=email, hashed_password=hash_password(DEMO_PASSWORD), role=role)
            db.add(u)
            users[role] = u
        db.flush()

        # Clients
        clients = [
            Client(business_name="Northwind Capital", legal_name="Northwind Capital Pvt Ltd",
                   email="finance@northwind.example", phone="+91 98200 11001",
                   billing_address="Bandra Kurla Complex, Mumbai", gst_number="27AABCN1234A1Z5",
                   coi="U65999MH2015PTC111111", category="Enterprise", notes="Quarterly retainer."),
            Client(business_name="Vertex Logistics", legal_name="Vertex Logistics Ltd",
                   email="ap@vertex.example", phone="+91 99100 22002",
                   billing_address="Whitefield, Bengaluru", gst_number="29AABCV5678B1Z3",
                   coi="U63030KA2018PLC222222", category="Mid-market"),
            Client(business_name="Halo SaaS", legal_name="Halo Software Inc",
                   email="billing@halo.example", phone="+91 90000 33003",
                   billing_address="HITEC City, Hyderabad", gst_number="36AAACH9012C1Z1",
                   category="Startup"),
        ]
        db.add_all(clients)
        db.flush()

        # Client invoices (+ payments to demonstrate locking)
        today = date.today()

        def make_invoice(num, client, taxable, rate, interstate, tds_rate, status, gst_status, days_ago):
            inv = ClientInvoice(
                invoice_number=num, client_id=client.id,
                invoice_date=today - timedelta(days=days_ago),
                due_date=today - timedelta(days=days_ago) + timedelta(days=30),
                service_description="Advisory & financial operations retainer",
                taxable_value=taxable, gst_rate=rate, is_interstate=interstate,
                tds_applicable=tds_rate > 0, tds_rate=tds_rate, status=status, gst_status=gst_status,
            )
            g = compute_gst(taxable, rate, interstate)
            inv.gst_amount, inv.cgst, inv.sgst, inv.igst = g.gst_amount, g.cgst, g.sgst, g.igst
            inv.total_amount = g.total
            inv.expected_tds = compute_tds(taxable, tds_rate, tds_rate > 0)
            inv.amount_pending = inv.total_amount
            return inv

        inv1 = make_invoice("INV-2026-0001", clients[0], 500000, 18, False, 10, InvoiceStatus.PENDING, GstStatus.COLLECTED, 40)
        inv2 = make_invoice("INV-2026-0002", clients[1], 320000, 18, True, 2, InvoiceStatus.PENDING, GstStatus.PENDING_COLLECTION, 25)
        inv3 = make_invoice("INV-2026-0003", clients[2], 150000, 18, False, 0, InvoiceStatus.SENT, GstStatus.PENDING_COLLECTION, 10)
        inv4 = make_invoice("INV-2026-0004", clients[0], 800000, 18, False, 10, InvoiceStatus.PENDING, GstStatus.RECONCILED, 70)
        db.add_all([inv1, inv2, inv3, inv4])
        db.flush()

        # Full payment on inv1 -> locks + Fully Paid
        db.add(Payment(invoice_id=inv1.id, amount=inv1.total_amount, payment_date=today - timedelta(days=5),
                       bank_reference="UTR5001NORTH", payment_mode=PaymentMode.RTGS, tds_deducted=inv1.expected_tds,
                       gst_component=inv1.gst_amount, remarks="Settled in full"))
        # Partial payment on inv2 -> locks + Partially Paid
        db.add(Payment(invoice_id=inv2.id, amount=150000, payment_date=today - timedelta(days=3),
                       bank_reference="UTR5002VERTEX", payment_mode=PaymentMode.NEFT, remarks="First tranche"))
        # Full on inv4
        db.add(Payment(invoice_id=inv4.id, amount=inv4.total_amount, payment_date=today - timedelta(days=20),
                       bank_reference="UTR5004NORTH", payment_mode=PaymentMode.RTGS, remarks="Cleared"))
        db.flush()
        for inv in (inv1, inv2, inv4):
            recompute_invoice(inv)
        inv4.status = InvoiceStatus.RECONCILED
        db.flush()

        # Vendors
        v1 = Vendor(business_name="Quanta Research", legal_name="Quanta Research LLP",
                    contact_person="Sara Iqbal", email="accounts@quanta.example", phone="+91 98765 40001",
                    address="Koramangala, Bengaluru", gst_number="29AAQFQ1111Q1Z9", pan="AAQFQ1111Q",
                    bank_account_holder="Quanta Research LLP", bank_name="HDFC Bank",
                    account_number="50100123456789", ifsc_code="HDFC0001234", tax_applicable=True,
                    approval_status=VendorApprovalStatus.VERIFIED, is_verified=True)
        v2 = Vendor(business_name="Lumen Media", email="finance@lumen.example", phone="+91 91234 50002",
                    address="Andheri, Mumbai", gst_number="27AALFL2222L1Z7", pan="AALFL2222L",
                    approval_status=VendorApprovalStatus.PENDING, is_verified=False)
        db.add_all([v1, v2])
        db.flush()

        alloc = VendorAllocation(vendor_id=v1.id, client_id=clients[0].id, project_name="Q3 Market Intelligence",
                                 scope_of_work="Sector research & dashboards", agreed_cost=240000, vendor_margin=22,
                                 allocation_percent=60, start_date=today - timedelta(days=30),
                                 end_date=today + timedelta(days=15), expected_report_date=today + timedelta(days=10),
                                 internal_owner="Daniel Roy", status=AllocationStatus.IN_PROGRESS)
        db.add(alloc)
        db.flush()

        vinv = VendorInvoice(vendor_id=v1.id, allocation_id=alloc.id, invoice_number="VINV-7781",
                             invoice_date=today - timedelta(days=8), invoice_amount=240000, gst_amount=43200,
                             tds_applicable=True, tds_rate=2.0)
        vinv.tds_amount = compute_tds(vinv.invoice_amount, vinv.tds_rate, True)
        vinv.net_payable = vendor_net_payable(vinv.invoice_amount, vinv.gst_amount, vinv.tds_amount)
        db.add(vinv)
        db.flush()

        # Report
        db.add(VendorReport(vendor_id=v1.id, client_id=clients[0].id, allocation_id=alloc.id,
                            project_name="Q3 Market Intelligence", reporting_period="Q3 2026", report_type="Research",
                            submission_date=today - timedelta(days=6), internal_reviewer="Daniel Roy",
                            review_status=ReportReviewStatus.APPROVED, remarks="Approved for client delivery."))

        # Payment approval — push it through to PAYMENT_READY
        appr = PaymentApproval(payee_name="Quanta Research LLP", amount=vinv.net_payable, purpose="Vendor payout · VINV-7781",
                               vendor_invoice_id=vinv.id, tax_deductions=vinv.tds_amount, net_payable=vinv.net_payable,
                               bank_details="HDFC Bank · 50100123456789 · HDFC0001234", requested_by_id=users[UserRole.FINANCE_MANAGER].id,
                               status=ApprovalStatus.PAYMENT_READY, cfo_comment="Within budget.", ceo_comment="Approved.")
        db.add(appr)

        # A second approval awaiting CFO
        appr2 = PaymentApproval(payee_name="Lumen Media", amount=90000, purpose="Creative production",
                                tax_deductions=1800, net_payable=88200, requested_by_id=users[UserRole.FINANCE_EXECUTIVE].id,
                                status=ApprovalStatus.SUBMITTED_CFO)
        db.add(appr2)
        db.flush()

        # Bank statement + transactions (one matches a payment, one matches the released approval, one mismatch)
        stmt = BankStatement(file_name="hdfc_june_2026.csv", bank_name="HDFC Bank", account_number="50100999999",
                             uploaded_by="Priya Nair", transaction_count=3, matched_count=2)
        db.add(stmt)
        db.flush()
        db.add_all([
            BankTransaction(statement_id=stmt.id, txn_date=today - timedelta(days=5), amount=inv1.total_amount,
                            utr_reference="UTR5001NORTH", narration="NEFT CR NORTHWIND", counterparty="Northwind Capital",
                            verification_status=VerificationStatus.AUTO_MATCHED, matched_payment_id=None,
                            match_note="Matched client payment"),
            BankTransaction(statement_id=stmt.id, txn_date=today - timedelta(days=2), amount=88000,
                            utr_reference="UTR9000XYZ", narration="UNKNOWN INWARD", counterparty="Unknown",
                            verification_status=VerificationStatus.MISMATCH, match_note="No matching record"),
            BankTransaction(statement_id=stmt.id, txn_date=today - timedelta(days=1), amount=150000,
                            utr_reference="UTR5002VERTEX", narration="NEFT CR VERTEX", counterparty="Vertex Logistics",
                            verification_status=VerificationStatus.RECONCILED, match_note="Reconciled"),
        ])

        db.commit()
        print("Seed complete.")
        print(f"  Users (password = {DEMO_PASSWORD}):")
        for name, email, role in USERS:
            print(f"    {role.value:<18} {email}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
