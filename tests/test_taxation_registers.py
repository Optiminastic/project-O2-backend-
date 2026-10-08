"""Taxation: GST on sales and purchases, TDS deducted by clients and by us, with tracking."""

from datetime import date

import pytest

from app.models import ClientInvoice, InvoiceStatus, Payment, Vendor
from app.services.taxation import tds_deposit_due_date
from tests.conftest import auth_header, default_service_id


def sale(db, client_row, *, number, invoice_date, taxable=1000.0, interstate=False, status=InvoiceStatus.SENT):
    gst = round(taxable * 0.18, 2)
    inv = ClientInvoice(
        client_id=client_row.id, agent_id=client_row.agent_id, service_id=default_service_id(),
        invoice_number=number, invoice_date=invoice_date, proforma_number=f"PI/{number}", status=status,
        taxable_value=taxable, gst_rate=18, is_interstate=interstate, gst_amount=gst,
        cgst=0 if interstate else gst / 2, sgst=0 if interstate else gst / 2, igst=gst if interstate else 0,
        total_amount=taxable + gst, amount_pending=taxable + gst, tds_rate=10, expected_tds=taxable * 0.1,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


@pytest.fixture
def vendors(db):
    rows = [
        Vendor(business_name="Quanta Research", email="ops@quanta.example", gst_number="29AAGFQ1234C1Z5", pan="AAGFQ1234C"),
        Vendor(business_name="Small Freelancer", email="me@free.example"),  # no GSTIN: no input credit
    ]
    db.add_all(rows)
    db.commit()
    for v in rows:
        db.refresh(v)
    return rows


def purchase(client, user, vendor, **overrides):
    body = {"vendor_id": vendor.id, "invoice_number": "B-1", "invoice_date": "2026-10-10", "invoice_amount": 10000,
            "gst_rate": 18, "is_interstate": False, "tds_section": "194C_FIRM", **overrides}
    return client.post("/api/vendors/invoices", json=body, headers=auth_header(user))


def get(client, user, path):
    return client.get(path, headers=auth_header(user))


# ---------- vendor invoice tax fields ----------

def test_vendor_bill_gst_is_split_and_tds_follows_the_section(client, users, vendors):
    res = purchase(client, users["exec"], vendors[0])
    assert res.status_code == 201, res.text
    bill = res.json()
    assert (bill["gst_amount"], bill["cgst"], bill["sgst"], bill["igst"]) == (1800, 900, 900, 0)
    assert bill["vendor_gstin"] == "29AAGFQ1234C1Z5"
    assert (bill["tds_section"], bill["tds_rate"], bill["tds_amount"]) == ("194C_FIRM", 2, 200)
    assert bill["net_payable"] == 11600

    inter = purchase(client, users["exec"], vendors[0], invoice_number="B-2", is_interstate=True,
                     tds_section="194J_PROFESSIONAL").json()
    assert (inter["cgst"], inter["igst"], inter["tds_rate"], inter["tds_amount"]) == (0, 1800, 10, 1000)


def test_unusual_gst_rates_and_unknown_sections_are_rejected(client, users, vendors):
    assert purchase(client, users["exec"], vendors[0], gst_rate=17).status_code == 422
    assert purchase(client, users["exec"], vendors[0], tds_section="999Z").status_code == 422


def test_no_tds_section_means_no_deduction(client, users, vendors):
    bill = purchase(client, users["exec"], vendors[0], tds_section="NONE").json()
    assert bill["tds_amount"] == 0
    assert bill["tds_applicable"] is False


# ---------- deposit due dates ----------

@pytest.mark.parametrize("deducted, due", [
    (date(2026, 10, 10), date(2026, 11, 7)),
    (date(2026, 12, 31), date(2027, 1, 7)),
    (date(2027, 3, 15), date(2027, 4, 30)),  # March deductions are due by 30 April
])
def test_tds_deposit_due_dates(deducted, due):
    assert tds_deposit_due_date(deducted) == due


# ---------- registers and summary ----------

def test_gst_summary_nets_input_credit_against_output(client, users, client_row, vendors, db):
    sale(db, client_row, number="INV/1", invoice_date=date(2026, 10, 5), taxable=100000)
    sale(db, client_row, number="INV/0", invoice_date=date(2026, 9, 5), taxable=50000)  # other month
    sale(db, client_row, number="INV/X", invoice_date=date(2026, 10, 6), taxable=70000, status=InvoiceStatus.CANCELLED)
    purchase(client, users["exec"], vendors[0])  # 1,800 input GST, eligible
    purchase(client, users["exec"], vendors[1], invoice_number="F-1")  # 1,800, not eligible (no GSTIN)

    s = get(client, users["manager"], "/api/taxation/summary?month=2026-10").json()
    assert s["output_gst"]["total"] == 18000
    assert s["output_gst"]["cgst"] == 9000
    assert s["input_gst"]["total"] == 1800
    assert s["input_gst_ineligible"] == 1800
    assert s["net_gst_payable"] == 16200
    assert s["gst_credit_carried"] == 0
    assert s["sales_count"] == 1
    assert s["purchase_count"] == 2


def test_more_input_than_output_is_carried_as_credit(client, users, client_row, vendors, db):
    sale(db, client_row, number="INV/1", invoice_date=date(2026, 10, 5), taxable=1000)  # 180 output
    purchase(client, users["exec"], vendors[0])  # 1,800 input
    s = get(client, users["manager"], "/api/taxation/summary?month=2026-10").json()
    assert s["net_gst_payable"] == 0
    assert s["gst_credit_carried"] == 1620


def test_sales_register_lists_each_issued_invoice(client, users, client_row, db):
    sale(db, client_row, number="INV/1", invoice_date=date(2026, 10, 5), taxable=1000, interstate=True)
    rows = get(client, users["manager"], "/api/taxation/gst/sales?month=2026-10").json()
    assert rows == [{
        "invoice_id": rows[0]["invoice_id"], "invoice_number": "INV/1", "invoice_date": "2026-10-05",
        "client_name": "Acme Traders", "client_gstin": "27BBBBB1111B1Z5", "taxable_value": 1000, "gst_rate": 18,
        "cgst": 0, "sgst": 0, "igst": 180, "gst_amount": 180, "total_amount": 1180,
    }]


def test_purchase_register_marks_input_credit_eligibility(client, users, vendors):
    purchase(client, users["exec"], vendors[0])
    purchase(client, users["exec"], vendors[1], invoice_number="F-1")
    rows = get(client, users["manager"], "/api/taxation/gst/purchases?month=2026-10").json()
    assert {r["vendor_name"]: r["itc_eligible"] for r in rows} == {"Quanta Research": True, "Small Freelancer": False}


def test_client_tds_register_and_certificate(client, users, client_row, db):
    inv = sale(db, client_row, number="INV/1", invoice_date=date(2026, 10, 5))
    db.add(Payment(invoice_id=inv.id, amount=1080, tds_deducted=100, payment_date=date(2026, 10, 20), bank_name="HDFC"))
    db.add(Payment(invoice_id=inv.id, amount=1, tds_deducted=0, payment_date=date(2026, 10, 21), bank_name="HDFC"))
    db.commit()

    rows = get(client, users["manager"], "/api/taxation/tds/clients?month=2026-10").json()
    assert len(rows) == 1  # payments without TDS are not listed
    assert (rows[0]["tds_deducted"], rows[0]["certificate_number"]) == (100, None)
    s = get(client, users["manager"], "/api/taxation/summary?month=2026-10").json()
    assert (s["client_tds_deducted"], s["client_tds_certificates_pending"]) == (100, 1)

    url = f"/api/taxation/client-tds/{rows[0]['payment_id']}/certificate"
    h = auth_header(users["manager"])
    assert client.patch(url, json={"certificate_number": "16A-Q3-001"}, headers=h).status_code == 422  # date needed too
    res = client.patch(url, json={"certificate_number": "16A-Q3-001", "certificate_date": "2026-11-15"}, headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["certificate_number"] == "16A-Q3-001"
    s = get(client, users["manager"], "/api/taxation/summary?month=2026-10").json()
    assert s["client_tds_certificates_pending"] == 0


def test_vendor_tds_register_deposit_and_overdue(client, users, vendors):
    old = purchase(client, users["exec"], vendors[0], invoice_number="OLD", invoice_date="2026-08-10").json()
    rows = get(client, users["manager"], "/api/taxation/tds/vendors?month=2026-08").json()
    assert rows[0]["due_date"] == "2026-09-07"
    assert rows[0]["overdue"] is True  # today is past 7 September 2026
    assert rows[0]["section_label"].startswith("194C")

    s = get(client, users["manager"], "/api/taxation/summary?month=2026-08").json()
    assert (s["vendor_tds_deducted"], s["vendor_tds_to_deposit"], s["vendor_tds_overdue_count"]) == (200, 200, 1)

    h = auth_header(users["manager"])
    url = f"/api/taxation/vendor-tds/{old['id']}/deposit"
    assert client.patch(url, json={"challan_number": "C1", "deposited_on": "2099-01-01"}, headers=h).status_code == 400
    res = client.patch(url, json={"challan_number": "28100123", "deposited_on": "2026-09-05"}, headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["overdue"] is False
    s = get(client, users["manager"], "/api/taxation/summary?month=2026-08").json()
    assert (s["vendor_tds_deposited"], s["vendor_tds_to_deposit"], s["vendor_tds_overdue_count"]) == (200, 0, 0)


def test_bills_without_tds_are_not_in_the_vendor_tds_register(client, users, vendors):
    purchase(client, users["exec"], vendors[0], tds_section="NONE")
    assert get(client, users["manager"], "/api/taxation/tds/vendors?month=2026-10").json() == []


def test_finance_executive_cannot_open_taxation(client, users):
    for path in ("summary", "gst/sales", "gst/purchases", "tds/clients", "tds/vendors"):
        assert get(client, users["exec"], f"/api/taxation/{path}").status_code == 403


def test_dashboard_shows_gst_payable(client, users, client_row, vendors, db):
    sale(db, client_row, number="INV/1", invoice_date=date(2026, 10, 5), taxable=100000)
    purchase(client, users["exec"], vendors[0])
    summary = get(client, users["ceo"], "/api/dashboard/summary?month=2026-10").json()
    assert summary["gst_payable"] == 16200
