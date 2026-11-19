"""Vendor bills carry Optiminastic's commission; outward payments pay chosen bills, split equally by service line."""

import pytest

from app.models import InvoiceStatus, ProjectVendor, ProjectVendorService, Service, Vendor, VendorInvoice
from app.services.numbering import business_date
from app.services.vendor_payouts import split_equally
from tests.conftest import assignment_for, auth_header


@pytest.fixture
def vendor(db):
    v = Vendor(business_name="Quanta Research", email="ops@quanta.example", gst_number="29AAGFQ1234C1Z5",
               commission_rate=10)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def second_code(db, vendor_id: int) -> int:
    """A second assigned service for the vendor on the same project as assignment_for()."""
    first = db.get(ProjectVendorService, assignment_for(vendor_id))
    pv = db.get(ProjectVendor, first.project_vendor_id)
    survey = Service(title="Field survey", sac_code="998371", gst_rate=18)
    db.add(survey)
    pv.last_service_seq += 1
    pv.services.append(ProjectVendorService(service=survey, code=f"{first.code[:-3]}S{pv.last_service_seq:02d}",
                                            agreed_cost=1_000_000))
    db.commit()
    return pv.services[-1].id


def bill(client, user, vendor_id, lines, number="B-1", **fields):
    body = {"vendor_id": vendor_id, "invoice_number": number, "invoice_date": business_date().isoformat(),
            "lines": [{"assignment_id": a, "amount": amt} for a, amt in lines], "gst_rate": 18,
            "tds_section": "194C_FIRM", **fields}
    return client.post("/api/vendors/invoices", json=body, headers=auth_header(user))


def payout(client, user, vendor_id, invoice_ids, amount, split=None):
    body = {"vendor_id": vendor_id, "vendor_invoice_ids": invoice_ids, "amount": amount}
    if split:
        body["split"] = split
    return client.post("/api/approvals/vendor-payout", headers=auth_header(user), json=body)


# ---------- commission ----------

def test_commission_is_kept_back_from_the_payout_not_the_tax(client, users, vendor):
    res = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 10000)])
    assert res.status_code == 201, res.text
    b = res.json()
    # GST and TDS stay on the vendor's full bill.
    assert (b["gst_amount"], b["tds_amount"], b["net_payable"]) == (1800, 200, 11600)
    assert (b["commission_rate"], b["commission_amount"], b["amount_payable"]) == (10, 1000, 10600)


def test_commission_can_be_changed_on_one_bill(client, users, vendor):
    b = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 10000)], commission_rate=0).json()
    assert (b["commission_amount"], b["amount_payable"]) == (0, 11600)
    assert bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 10000)], number="B-2",
                commission_rate=101).status_code == 422


def test_vendor_commission_rate_is_set_on_the_vendor(client, users, vendor):
    res = client.patch(f"/api/vendors/{vendor.id}", json={"commission_rate": 12.5}, headers=auth_header(users["manager"]))
    assert res.status_code == 200 and res.json()["commission_rate"] == 12.5
    # An explicit null leaves it as it is.
    assert client.patch(f"/api/vendors/{vendor.id}", json={"commission_rate": None},
                        headers=auth_header(users["manager"])).json()["commission_rate"] == 12.5


# ---------- splitting a payment equally ----------

def test_split_is_equal_when_every_line_can_take_its_share():
    assert split_equally(300, [("a", 500), ("b", 500), ("c", 500)]) == {"a": 100, "b": 100, "c": 100}


def test_a_line_owing_less_than_its_share_is_settled_and_the_rest_share_the_leftover():
    assert split_equally(300, [("a", 50), ("b", 500), ("c", 500)]) == {"a": 50, "b": 125, "c": 125}


def test_split_places_every_paisa():
    shares = split_equally(100, [("a", 100), ("b", 100), ("c", 100)])
    assert round(sum(shares.values()), 2) == 100
    assert sorted(shares.values()) == [33.33, 33.33, 33.34]


def test_split_cannot_exceed_what_is_owed():
    with pytest.raises(ValueError):
        split_equally(101, [("a", 50), ("b", 50)])


# ---------- outward payments ----------

def test_payment_over_several_bills_is_split_equally_by_service_line(client, users, vendor, db):
    first, second = assignment_for(vendor.id), second_code(db, vendor.id)
    a = bill(client, users["exec"], vendor.id, [(first, 10000), (second, 10000)], commission_rate=0).json()  # 23,200 due
    b = bill(client, users["exec"], vendor.id, [(first, 1000)], number="B-2", commission_rate=0).json()  # 1,160 due
    payables = {p["invoice_number"]: p["due"] for p in client.get(f"/api/vendors/{vendor.id}/payables",
                                                                   headers=auth_header(users["exec"])).json()}
    assert payables == {"B-1": 23200, "B-2": 1160}

    res = payout(client, users["manager"], vendor.id, [a["id"], b["id"]], 6000)
    assert res.status_code == 201, res.text
    p = res.json()
    assert (p["payee_name"], p["vendor_id"], p["amount"], p["net_payable"]) == ("Quanta Research", vendor.id, 6000, 6000)
    # Three lines: B-2's single line owes only 1,160, the other two share the rest equally.
    split = sorted((x["invoice_number"], x["amount"]) for x in p["allocations"])
    assert split == [("B-1", 2420), ("B-1", 2420), ("B-2", 1160)]
    assert all(x["code"] for x in p["allocations"])


def test_payment_cannot_exceed_what_the_selected_bills_still_owe(client, users, vendor):
    a = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 10000)]).json()  # 10,600 payable
    res = payout(client, users["manager"], vendor.id, [a["id"]], 10600.01)
    assert res.status_code == 400 and "more than" in res.json()["detail"]
    assert payout(client, users["manager"], vendor.id, [a["id"]], 6000).status_code == 201
    # 6,000 is already on its way: only 4,600 is left.
    assert payout(client, users["manager"], vendor.id, [a["id"]], 4601).status_code == 400
    assert payout(client, users["manager"], vendor.id, [a["id"]], 4600).status_code == 201
    assert payout(client, users["manager"], vendor.id, [a["id"]], 1).status_code == 400


def test_a_rejected_payment_frees_the_bill_again(client, users, vendor):
    a = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 10000)]).json()
    first = payout(client, users["manager"], vendor.id, [a["id"]], 10600).json()
    client.post(f"/api/approvals/{first['id']}/submit", headers=auth_header(users["manager"]))
    client.post(f"/api/approvals/{first['id']}/ceo", json={"approve": False, "comment": "Wrong bank"},
                headers=auth_header(users["ceo"]))
    assert payout(client, users["manager"], vendor.id, [a["id"]], 10600).status_code == 201
    # The rejected one cannot be resubmitted now that the bill is covered again.
    res = client.post(f"/api/approvals/{first['id']}/submit", headers=auth_header(users["manager"]))
    assert res.status_code == 400


def test_releasing_a_payment_marks_the_bills_paid(client, users, vendor, db):
    a = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 10000)]).json()

    def pay(amount, utr):
        p = payout(client, users["manager"], vendor.id, [a["id"]], amount).json()
        client.post(f"/api/approvals/{p['id']}/submit", headers=auth_header(users["manager"]))
        client.post(f"/api/approvals/{p['id']}/ceo", json={"approve": True}, headers=auth_header(users["ceo"]))
        res = client.post(f"/api/approvals/{p['id']}/release", json={"payment_reference": utr},
                          headers=auth_header(users["manager"]))
        assert res.status_code == 200, res.text

    pay(5000, "UTR1001")
    db.expire_all()
    assert db.get(VendorInvoice, a["id"]).status == InvoiceStatus.PARTIALLY_PAID
    pay(5600, "UTR1002")
    db.expire_all()
    assert db.get(VendorInvoice, a["id"]).status == InvoiceStatus.FULLY_PAID
    job = client.get(f"/api/vendors/{vendor.id}/assignments", headers=auth_header(users["exec"])).json()[0]
    assert (job["invoice_status"], job["paid"]) == ("Partly invoiced", 10600)


def test_a_payment_pays_only_the_vendors_own_bills(client, users, vendor, db):
    other = Vendor(business_name="Field Force", email="hello@fieldforce.example")
    db.add(other)
    db.commit()
    theirs = bill(client, users["exec"], other.id, [(assignment_for(other.id), 1000)]).json()
    assert payout(client, users["manager"], vendor.id, [theirs["id"]], 100).status_code == 400
    assert payout(client, users["exec"], other.id, [theirs["id"]], 100).status_code == 403


# ---------- jobs and documents ----------

def test_job_status_follows_billing(client, users, vendor):
    code = assignment_for(vendor.id, agreed_cost=10000)
    status = lambda: client.get(f"/api/vendors/{vendor.id}/assignments", headers=auth_header(users["exec"])).json()[0]["invoice_status"]  # noqa: E731
    assert status() == "Not invoiced"
    bill(client, users["exec"], vendor.id, [(code, 4000)])
    assert status() == "Partly invoiced"
    bill(client, users["exec"], vendor.id, [(code, 6000)], number="B-2")
    assert status() == "Fully invoiced"
    bill(client, users["exec"], vendor.id, [(code, 1)], number="B-3")
    assert status() == "Over-invoiced"


def test_vendor_invoice_document_upload_and_download(client, users, vendor):
    b = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 1000)]).json()
    url, h = f"/api/vendors/invoices/{b['id']}/document", auth_header(users["exec"])
    assert client.get(url, headers=h).status_code == 404
    bad = client.post(url, headers=h, files={"file": ("notes.txt", b"hello", "text/plain")})
    assert bad.status_code == 400
    res = client.post(url, headers=h, files={"file": ("B-1.pdf", b"%PDF-1.4 demo", "application/pdf")})
    assert res.status_code == 200, res.text
    assert res.json()["document_name"] == "B-1.pdf"
    got = client.get(url, headers=h)
    assert got.status_code == 200 and got.content == b"%PDF-1.4 demo"


# ---------- supplier details on the bill ----------

def test_bill_keeps_the_supplier_details_as_printed(client, users, vendor):
    b = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 1000)],
             supplier_gstin="27aagfq1234c1zo", supplier_name="Quanta Research LLP",
             supplier_address="12 MG Road, Bengaluru 560001", supplier_coi="AAB-1234").json()
    assert (b["vendor_gstin"], b["supplier_name"], b["supplier_address"], b["supplier_coi"]) == (
        "27AAGFQ1234C1ZO", "Quanta Research LLP", "12 MG Road, Bengaluru 560001", "AAB-1234",
    )


def test_bill_supplier_details_default_to_the_vendor(client, users, vendor):
    b = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 1000)]).json()
    assert (b["vendor_gstin"], b["supplier_name"]) == ("29AAGFQ1234C1Z5", "Quanta Research")


def test_bill_rejects_a_bad_supplier_gstin(client, users, vendor):
    res = bill(client, users["exec"], vendor.id, [(assignment_for(vendor.id), 1000)], supplier_gstin="29AAGFQ1234C1ZX")
    assert res.status_code == 422


def test_payment_can_pay_the_oldest_bill_first(client, users, vendor, db):
    first, second = assignment_for(vendor.id), second_code(db, vendor.id)
    newer = bill(client, users["exec"], vendor.id, [(first, 10000)], number="NEW-1", commission_rate=0).json()  # 11,600 due
    older = bill(client, users["exec"], vendor.id, [(first, 1000), (second, 1000)], number="OLD-1",
                 invoice_date="2026-11-01", commission_rate=0).json()  # 2,320 due, 1,160 per line
    res = payout(client, users["manager"], vendor.id, [newer["id"], older["id"]], 3000, split="oldest")
    assert res.status_code == 201, res.text
    split = sorted((a["invoice_number"], a["amount"]) for a in res.json()["allocations"])
    # The older bill is paid in full (both lines), the rest goes to the newer one.
    assert split == [("NEW-1", 680), ("OLD-1", 1160), ("OLD-1", 1160)]
    assert payout(client, users["manager"], vendor.id, [newer["id"]], 1, split="random").status_code == 422
