"""Add demo data for trying vendors, services and project assignments locally.

Run inside the API container:  python -m app.demo_data

Unlike ``app.seed`` this never drops anything: it only adds, and it goes through
the real API in-process, so every rule (codes, numbering, GST, TDS, approvals)
applies exactly as it does in the app. It needs two CEO/CFO/Finance Manager
accounts (the second issues tax invoices, because the creator cannot), and it
refuses to run twice.

Adds:
  * 2 clients, 5 vendors with the services they provide, 1 company bank
  * 3 projects with vendors assigned per service (codes like DEMO-RETAIL-V01-S01)
  * vendor bills charged against those codes (one over the agreed cost)
  * client proformas at each stage, one issued as a tax invoice with a receipt
  * vendor payouts: one released, one awaiting the CEO
"""

from __future__ import annotations

import sys
from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.database import SessionLocal
from app.main import app
from app.models import Service, User, UserRole, Vendor, VendorInvoice
from app.services.identifiers import gstin_check_char
from app.services.numbering import business_date

MARKER_EMAIL = "studio@pixelforge.example"
MANAGER_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)


def gstin(state: str, pan: str) -> str:
    first14 = f"{state}{pan}1Z"
    return first14 + gstin_check_char(first14)


def bank(holder: str, bank_name: str, number: str, ifsc: str) -> dict:
    return {"account_holder": holder, "bank_name": bank_name, "account_number": number, "ifsc_code": ifsc}


class Api:
    def __init__(self, client: TestClient, user: User):
        self.client = client
        self.headers = {"Authorization": f"Bearer {create_access_token(user.email, user.role.value, user.name)}"}

    def call(self, method: str, path: str, body: dict | None = None, ok: tuple[int, ...] = (200, 201)) -> dict:
        res = self.client.request(method, f"/api{path}", json=body, headers=self.headers)
        if res.status_code not in ok:
            raise SystemExit(f"{method} {path} failed ({res.status_code}): {res.text}")
        return res.json() if res.content else {}

    def post(self, path, body=None):
        return self.call("POST", path, body)

    def put(self, path, body=None):
        return self.call("PUT", path, body)


COMMISSION_MARKER = "PF/26-27/052"
COMMISSION_RATES = {  # Optiminastic's commission per demo vendor, by email
    MARKER_EMAIL: 10, "billing@reachdigital.example": 8, "finance@datalens.example": 12,
    "accounts@codecraft.example": 5, "hello@momentum-events.example": 10,
}
# A one-page PDF to stand in for a vendor's uploaded invoice.
SAMPLE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 120]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj "
    b"4 0 obj<</Length 58>>stream\nBT /F1 14 Tf 20 60 Td (PixelForge invoice PF/26-27/052) Tj ET\nendstream endobj "
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)


def add_commissions_and_payouts() -> None:
    """Second demo step: commission rates, bills net of commission (one with its PDF), a split payout."""
    with SessionLocal() as db:
        if db.query(VendorInvoice).filter(VendorInvoice.invoice_number == COMMISSION_MARKER).first():
            print("Commission and payout demo data is already there. Nothing added.")
            return
        vendors = {v.email: v.id for v in db.query(Vendor).filter(Vendor.email.in_(COMMISSION_RATES)).all()}
        maker = db.query(User).filter(User.role.in_(MANAGER_ROLES), User.is_active.is_(True)).order_by(User.id.desc()).first()
    if len(vendors) < len(COMMISSION_RATES) or maker is None:
        sys.exit("Run the first demo step first (python -m app.demo_data on an empty demo).")
    today = business_date()

    with TestClient(app) as client:
        api = Api(client, maker)
        for email, rate in COMMISSION_RATES.items():
            api.call("PATCH", f"/vendors/{vendors[email]}", {"commission_rate": rate})

        def codes(email, project_code):
            return {a["service_title"]: a["id"] for a in api.call("GET", f"/vendors/{vendors[email]}/assignments")
                    if a["project_code"] == project_code}

        def bill(email, number, lines):
            return api.post("/vendors/invoices", {
                "vendor_id": vendors[email], "invoice_number": number, "invoice_date": today.isoformat(),
                "lines": [{"assignment_id": a, "amount": amt} for a, amt in lines], "gst_rate": 18,
            })

        pixel = codes(MARKER_EMAIL, "DEMO-BRAND")
        design = next(i for t, i in pixel.items() if t.startswith("Creative"))
        video = next(i for t, i in pixel.items() if t.startswith("Video"))
        brand_bill = bill(MARKER_EMAIL, COMMISSION_MARKER, [(design, 60000), (video, 40000)])
        res = client.post(f"/api/vendors/invoices/{brand_bill['id']}/document", headers=api.headers,
                          files={"file": ("PF-26-27-052.pdf", SAMPLE_PDF, "application/pdf")})
        if res.status_code != 200:
            raise SystemExit(f"Uploading the sample invoice failed ({res.status_code}): {res.text}")

        datalens_email = "finance@datalens.example"
        analytics = next(iter(codes(datalens_email, "DEMO-RETAIL").values()))
        new_bill = bill(datalens_email, "DL-2026-131", [(analytics, 50000)])
        old_bill = next(b for b in api.call("GET", "/vendors/invoices/all") if b["invoice_number"] == "DL-2026-118")
        payout = api.post("/approvals/vendor-payout", {
            "vendor_id": vendors[datalens_email], "vendor_invoice_ids": [old_bill["id"], new_bill["id"]],
            "amount": 100000, "bank_details": "Axis Bank · 917020045566778 · UTIB0000917",
        })
        api.post(f"/approvals/{payout['id']}/submit")

    print("Commission and payout demo data added:")
    print("  Commission  PixelForge 10%, Reach 8%, DataLens 12%, CodeCraft 5%, Momentum 10% (new bills only)")
    print(f"  Bill {COMMISSION_MARKER}  PixelForge on DEMO-BRAND, 2 UPS codes, {brand_bill['commission_amount']:,.0f} commission, "
          f"paid to vendor {brand_bill['amount_payable']:,.0f}, PDF attached")
    split = ", ".join(f"{a['code'] or a['invoice_number']}: {a['amount']:,.2f}" for a in payout["allocations"])
    print(f"  Payout     DataLens 1,00,000 over DL-2026-118 + DL-2026-131 -> {split} (awaiting CEO)")


RECEIPTS_MARKER = "Demo: open invoices for the receipt split"


def add_open_tax_invoices() -> None:
    """Third demo step: several open tax invoices per client, each billing more than one service,
    so an inward payment over two of them can be split equally across services."""
    from app.models import ClientInvoice, Project  # noqa: PLC0415

    with SessionLocal() as db:
        if db.query(ClientInvoice).filter(ClientInvoice.internal_remarks == RECEIPTS_MARKER).first():
            print("Open tax invoices for the receipt split are already there. Nothing added.")
            return
        projects = {p.code: p.id for p in db.query(Project).filter(Project.code.in_(["DEMO-FINAPP", "DEMO-BRAND"])).all()}
        managers = db.query(User).filter(User.role.in_(MANAGER_ROLES), User.is_active.is_(True)).order_by(User.id).all()
        services = {s.title: s.id for s in db.query(Service).all()}
    if len(projects) < 2 or len(managers) < 2:
        sys.exit("Run the first demo step first (python -m app.demo_data on an empty demo).")
    sid = lambda prefix: next(i for t, i in services.items() if t.startswith(prefix))  # noqa: E731

    with TestClient(app) as client:
        maker, checker = Api(client, managers[-1]), Api(client, managers[0])

        def issue(invoice_id: int, note: str) -> dict:
            invoice = maker.call("GET", f"/invoices/{invoice_id}")
            if invoice["status"] in ("Draft", "Changes Requested"):
                maker.post(f"/invoices/{invoice_id}/send-to-client")
            if maker.call("GET", f"/invoices/{invoice_id}")["status"] == "Awaiting Client":
                maker.post(f"/invoices/{invoice_id}/client-approval", {"note": note})
            return checker.post(f"/invoices/{invoice_id}/issue")["invoice"]

        def proforma(project_code: str, *lines) -> dict:
            return maker.post("/invoices", {
                "project_id": projects[project_code], "internal_remarks": RECEIPTS_MARKER,
                "lines": [{"service_id": s, "taxable_value": v} for s, v in lines],
            })

        issued = []
        # Proformas already raised for these projects that are not issued yet.
        for code in ("DEMO-FINAPP", "DEMO-BRAND"):
            for inv in maker.call("GET", "/invoices"):
                if inv["project_id"] == projects[code] and not inv["is_issued"] and inv["status"] != "Cancelled":
                    issued.append(issue(inv["id"], "Approved on the client's call."))
        # One more for each client, with several services.
        issued.append(issue(proforma("DEMO-FINAPP", (sid("Information Technology"), 250000),
                                     (sid("Technology & Development"), 150000), (sid("Website / Application"), 200000))["id"],
                            "Approved by Northstar's IT head by email."))
        issued.append(issue(proforma("DEMO-BRAND", (sid("Creative Design"), 100000), (sid("Video Production"), 150000))["id"],
                            "Approved by Skyline's brand team by email."))

    print("Open tax invoices added for the receipt split:")
    for inv in issued:
        services_ = ", ".join(line["service_title"].split(" ")[0] for line in inv["lines"])
        print(f"  {inv['invoice_number']:<18} total {inv['total_amount']:>12,.2f}  pending {inv['amount_pending']:>12,.2f}  "
              f"{len(inv['lines'])} services ({services_})")


def main() -> None:
    with SessionLocal() as db:
        base_exists = db.query(Vendor).filter(Vendor.email == MARKER_EMAIL).first() is not None
    if base_exists:
        print("Base demo data is already there.")
    else:
        add_base_demo()
    add_commissions_and_payouts()
    add_open_tax_invoices()


def add_base_demo() -> None:
    with SessionLocal() as db:
        managers = db.query(User).filter(User.role.in_(MANAGER_ROLES), User.is_active.is_(True)).order_by(User.id).all()
        if len(managers) < 2:
            sys.exit("Needs two active CEO / CFO / Finance Manager accounts (one raises invoices, the other issues them).")
        maker, checker = managers[-1], managers[0]
        services = {s.title: s.id for s in db.query(Service).all()}

    def sid(prefix: str) -> int:
        match = [i for title, i in services.items() if title.startswith(prefix)]
        if not match:
            sys.exit(f"The service catalogue has no service starting with '{prefix}'.")
        return match[0]

    marketing, design, analytics = sid("Digital Marketing"), sid("Creative Design"), sid("Data Analytics")
    app_dev, it_consulting, tech_support = sid("Website / Application"), sid("Information Technology"), sid("Technology & Development")
    video, integrated, strategy = sid("Video Production"), sid("Integrated Marketing"), sid("Consulting & Strategy")

    today = business_date()
    month_start = today.replace(day=1)
    days_ago = lambda n: max(today - timedelta(days=n), month_start).isoformat()  # noqa: E731 - keep demo bills in this month

    with TestClient(app) as client:
        api, issuer = Api(client, maker), Api(client, checker)

        # ---- clients ----
        skyline = api.post("/clients", {
            "business_name": "Skyline Retail", "legal_name": "Skyline Retail Private Limited",
            "email": "accounts@skyline-retail.example", "phone": "+91 98200 10001",
            "gst_number": gstin("27", "AADCS4821K"), "billing_address": "14 Linking Road, Bandra West, Mumbai 400050",
            "coi": "U52100MH2016PTC281734", "msme": False,
        })
        northstar = api.post("/clients", {
            "business_name": "Northstar Finance", "legal_name": "Northstar Finance Limited",
            "email": "ap@northstar-finance.example", "phone": "+91 99100 20002",
            "gst_number": gstin("29", "AAECN7310P"), "billing_address": "88 MG Road, Bengaluru 560001",
            "coi": "L65910KA2011PLC059342", "msme": True, "msme_number": "UDYAM-KA-03-0045127",
        })

        # ---- vendors and the services they provide ----
        def vendor(name, email, state, pan, address, services_, holder, bank_name, number, ifsc, tds="194J_TECHNICAL"):
            return api.post("/vendors", {
                "business_name": name, "email": email, "gst_number": gstin(state, pan), "address": address,
                "msme": False, "bank_accounts": [bank(holder, bank_name, number, ifsc)],
                "default_tds_section": tds, "service_ids": services_,
            })

        pixel = vendor("PixelForge Studios", MARKER_EMAIL, "27", "AAKFP5521M", "Lower Parel, Mumbai 400013",
                       [design, video], "PixelForge Studios LLP", "HDFC Bank", "50200011223344", "HDFC0000123")
        reach = vendor("Reach Digital", "billing@reachdigital.example", "27", "AAGCR8812Q", "Andheri East, Mumbai 400069",
                       [marketing, integrated], "Reach Digital Pvt Ltd", "ICICI Bank", "001405667788", "ICIC0000014",
                       tds="194C_FIRM")
        datalens = vendor("DataLens Analytics", "finance@datalens.example", "29", "AAHCD3390L", "Koramangala, Bengaluru 560034",
                          [analytics, it_consulting, strategy], "DataLens Analytics Pvt Ltd", "Axis Bank",
                          "917020045566778", "UTIB0000917", tds="194J_PROFESSIONAL")
        codecraft = vendor("CodeCraft Labs", "accounts@codecraft.example", "29", "AAJCC6042R", "HSR Layout, Bengaluru 560102",
                           [app_dev, tech_support], "CodeCraft Labs Pvt Ltd", "Kotak Mahindra Bank", "8611223344", "KKBK0000861")
        momentum = vendor("Momentum Events", "hello@momentum-events.example", "07", "AAMFM2207C", "Saket, New Delhi 110017",
                          [marketing, integrated, video], "Momentum Events", "State Bank of India", "38211002299",
                          "SBIN0001234", tds="194C_FIRM")

        # Only verified vendors can be paid: verify all but Momentum (left to show the check).
        for v in (pixel, reach, datalens, codecraft):
            api.post(f"/vendors/{v['id']}/verify")

        # ---- projects, with vendors assigned per service ----
        def assign(vendor_row, *lines):
            return {"vendor_id": vendor_row["id"], "services": [{"service_id": s, "agreed_cost": c} for s, c in lines]}

        retail = api.post("/projects", {
            "code": "DEMO-RETAIL", "title": "Festive season digital campaign", "brand": "Skyline Retail",
            "description": "Paid social and search for the festive season, creatives for 4 cities, weekly dashboards.",
            "client_id": skyline["id"], "service_ids": [marketing, design, analytics],
            "budget": 700000, "contract_value": 1200000, "start_date": month_start.isoformat(),
            "end_date": (month_start + timedelta(days=75)).isoformat(),
            "vendors": [assign(pixel, (design, 150000)), assign(reach, (marketing, 300000)),
                        assign(datalens, (analytics, 120000))],
        })
        finapp = api.post("/projects", {
            "code": "DEMO-FINAPP", "title": "Customer self-service app", "brand": "Northstar Finance",
            "description": "Mobile app for loan statements and payments, with 6 months of support.",
            "client_id": northstar["id"], "service_ids": [app_dev, it_consulting, tech_support],
            "budget": 1100000, "contract_value": 1800000,
            "start_date": (month_start - timedelta(days=31)).replace(day=1).isoformat(),
            "end_date": (month_start + timedelta(days=150)).isoformat(),
            "vendors": [assign(codecraft, (app_dev, 600000), (tech_support, 150000)),
                        assign(datalens, (it_consulting, 200000))],
        })
        brand = api.post("/projects", {
            "code": "DEMO-BRAND", "title": "Brand refresh film", "brand": "Skyline Retail",
            "description": "New brand identity and a 60-second brand film.",
            "client_id": skyline["id"], "service_ids": [design, video],
            "budget": 300000, "contract_value": 500000, "start_date": month_start.isoformat(),
            "end_date": (month_start + timedelta(days=45)).isoformat(),
            "vendors": [assign(pixel, (design, 100000), (video, 120000))],
        })

        def code_id(project, vendor_row, service_id):
            pv = next(v for v in project["vendors"] if v["vendor_id"] == vendor_row["id"])
            return next(s["id"] for s in pv["services"] if s["service_id"] == service_id)

        # ---- vendor bills against the codes ----
        def bill(vendor_row, number, days, *lines, tds=None):
            body = {"vendor_id": vendor_row["id"], "invoice_number": number, "invoice_date": days_ago(days),
                    "lines": [{"assignment_id": a, "amount": amt} for a, amt in lines], "gst_rate": 18}
            if tds:
                body["tds_section"] = tds
            return api.post("/vendors/invoices", body)

        pixel_bill = bill(pixel, "PF/26-27/041", 6, (code_id(retail, pixel, design), 90000), tds="194J_TECHNICAL")
        bill(reach, "RD-1187", 4, (code_id(retail, reach, marketing), 320000), tds="194C_FIRM")  # over the 3,00,000 agreed
        code_bill = bill(codecraft, "CCL/0932", 3, (code_id(finapp, codecraft, app_dev), 300000),
                         (code_id(finapp, codecraft, tech_support), 50000), tds="194J_TECHNICAL")
        bill(datalens, "DL-2026-118", 2, (code_id(finapp, datalens, it_consulting), 80000), tds="194J_PROFESSIONAL")

        # ---- client invoices at each stage ----
        def proforma(project, *lines):
            return api.post("/invoices", {"project_id": project["id"],
                                          "lines": [{"service_id": s, "taxable_value": v} for s, v in lines]})

        retail_pi = proforma(retail, (marketing, 500000), (design, 250000), (analytics, 200000))
        api.post(f"/invoices/{retail_pi['id']}/send-to-client")
        api.post(f"/invoices/{retail_pi['id']}/client-approval", {"note": "Approved by email from Skyline's marketing head."})
        issued = issuer.post(f"/invoices/{retail_pi['id']}/issue")

        finapp_pi = proforma(finapp, (app_dev, 800000), (tech_support, 200000))
        api.post(f"/invoices/{finapp_pi['id']}/send-to-client")  # awaiting the client
        proforma(brand, (design, 150000))  # a draft

        # ---- a receipt on the tax invoice ----
        company_bank = api.post("/banks", {"name": "HDFC Bank (current account)", "account_last4": "4417"})
        api.post(f"/invoices/{retail_pi['id']}/payments", {
            "amount": 500000, "payment_date": today.isoformat(), "bank_account_id": company_bank["id"],
            "bank_reference": "UTR26DEMO0001",
        })

        # ---- vendor payouts ----
        released = api.post("/approvals", {
            "payee_name": "PixelForge Studios LLP", "amount": pixel_bill["net_payable"],
            "purpose": "Payout · PF/26-27/041", "vendor_invoice_id": pixel_bill["id"],
            "tax_deductions": pixel_bill["tds_amount"], "bank_details": "HDFC Bank · 50200011223344 · HDFC0000123",
        })
        api.post(f"/approvals/{released['id']}/submit")
        api.post(f"/approvals/{released['id']}/ceo", {"approve": True, "comment": "Within the agreed cost."})
        api.post(f"/approvals/{released['id']}/release", {"payment_reference": "UTR26DEMOPAY01"})
        waiting = api.post("/approvals", {
            "payee_name": "CodeCraft Labs Pvt Ltd", "amount": code_bill["net_payable"],
            "purpose": "Payout · CCL/0932", "vendor_invoice_id": code_bill["id"], "tax_deductions": code_bill["tds_amount"],
        })
        api.post(f"/approvals/{waiting['id']}/submit")

    print("Demo data added:")
    print("  Clients   Skyline Retail, Northstar Finance")
    print("  Vendors   PixelForge Studios, Reach Digital, DataLens Analytics, CodeCraft Labs, Momentum Events (unassigned)")
    for p in (retail, finapp, brand):
        codes = ", ".join(s["code"] for v in p["vendors"] for s in v["services"])
        print(f"  {p['code']:<12} {codes}")
    print(f"  Tax invoice {issued['invoice']['invoice_number']} issued (by {checker.email}), "
          "one proforma awaiting the client, one draft")


if __name__ == "__main__":
    main()
