"""Finance input rules: Indian tax identifiers, rupee amounts, GST slabs and dates."""

from datetime import timedelta

import pytest

from app.services.identifiers import gstin_check_char, gstin_error
from app.services.numbering import business_date
from tests.conftest import auth_header, default_service_id
from tests.test_payment_bank import issued_invoice

GSTIN = "27AAGFQ1234C1ZO"  # valid checksum; PAN AAGFQ1234C
TODAY = business_date()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


# ---------- identifiers ----------


def test_gstin_checksum_matches_published_numbers():
    for gstin in ("27AAPFU0939F1ZV", "29AAGCB7383J1Z4", "07AAGFF2194N1Z1", GSTIN):
        assert gstin_error(gstin) is None, gstin
    assert gstin_check_char("27AAGFQ1234C1Z") == "O"


@pytest.mark.parametrize("gstin, reason", [
    ("27AAGFQ1234C1Z5", "check character"),
    ("27AAGFQ1234C1Z", "15 characters"),
    ("45AAGFQ1234C1ZO", "state code"),
    ("27AAGQQ1234C1ZO", "format"),  # Q is not a PAN holder type
])
def test_bad_gstins_say_why(gstin, reason):
    assert reason in gstin_error(gstin)


# ---------- clients ----------


def new_client(client, user, **fields):
    body = {"business_name": "Globex", "email": "ap@globex.example", **fields}
    return client.post("/api/clients", json=body, headers=auth_header(user))


def test_client_identifiers_are_cleaned_and_kept(client, users):
    res = new_client(client, users["exec"], gst_number=" 27aagfq1234c1zo ", phone="+91 98100-00000",
                     coi="u72900mh2015ptc123456")
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["gst_number"], body["phone"], body["coi"]) == (GSTIN, "+919810000000", "U72900MH2015PTC123456")


@pytest.mark.parametrize("field, value", [
    ("gst_number", "hgfhfhfh"),
    ("gst_number", "27AAGFQ1234C1Z5"),
    ("phone", "78787678"),
    ("phone", "5810000000"),  # Indian mobiles start with 6 to 9
    ("coi", "not a cin"),
    ("business_name", "   "),
    ("business_name", "Line one\nLine two"),
])
def test_client_rejects_bad_details(client, users, field, value):
    assert new_client(client, users["exec"], **{field: value}).status_code == 422


def test_blank_optional_fields_are_stored_as_empty(client, users):
    res = new_client(client, users["exec"], gst_number="", phone="  ", coi="")
    assert res.status_code == 201, res.text
    assert res.json()["gst_number"] is None


# ---------- vendors and agents ----------


def test_vendor_pan_must_match_the_pan_inside_its_gstin(client, users):
    body = {"business_name": "Quanta", "email": "a@quanta.example", "gst_number": GSTIN, "pan": "AAGFQ9999C"}
    res = client.post("/api/vendors", json=body, headers=auth_header(users["exec"]))
    assert res.status_code == 422
    assert "does not match" in res.text


def test_vendor_cannot_list_the_same_bank_account_twice(client, users):
    account = {"account_holder": "Quanta", "bank_name": "HDFC", "account_number": "50100123456789", "ifsc_code": "HDFC0001234"}
    body = {"business_name": "Quanta", "email": "a@quanta.example", "bank_accounts": [account, account]}
    assert client.post("/api/vendors", json=body, headers=auth_header(users["exec"])).status_code == 422


@pytest.mark.parametrize("fields", [
    {"commission_rate": 150},
    {"commission_rate": -1},
    {"account_number": "50100123456789"},  # no IFSC or holder
    {"ifsc_code": "HDFC1234567"},  # 5th character must be 0
    {"pan": "ABCDE12345"},
])
def test_agent_rejects_bad_details(client, users, fields):
    body = {"business_name": "Techcores", "email": "t@techcores.example", **fields}
    assert client.post("/api/agents", json=body, headers=auth_header(users["ceo"])).status_code == 422


# ---------- invoices ----------


def new_invoice(client, user, client_row, **fields):
    body = {"client_id": client_row.id, "service_id": default_service_id(), "taxable_value": 1000, **fields}
    return client.post("/api/invoices", json=body, headers=auth_header(user))


@pytest.mark.parametrize("fields", [
    {"taxable_value": 0},
    {"taxable_value": -10},
    {"taxable_value": 100.555},  # paise have two decimals
    {"taxable_value": 1e12},  # above the ₹10,000 crore ceiling
    {"gst_rate": 15},  # not a GST slab
    {"tds_rate": 25},
    {"sac_code": "1234"},
    {"due_date": (TODAY - timedelta(days=1)).isoformat()},
])
def test_invoice_rejects_bad_amounts_rates_and_dates(client, users, client_row, fields):
    assert new_invoice(client, users["exec"], client_row, **fields).status_code == 422


def test_invoice_accepts_a_valid_slab_and_sac(client, users, client_row):
    res = new_invoice(client, users["exec"], client_row, taxable_value=1234.5, gst_rate=5, sac_code="998312")
    assert res.status_code == 201, res.text
    assert res.json()["gst_amount"] == 61.73


# ---------- receipts ----------


def receipt(client, user, invoice_id, **fields):
    body = {"amount": 500, "payment_date": TODAY.isoformat(), "bank_name": "HDFC Bank", **fields}
    return client.post(f"/api/invoices/{invoice_id}/payments", json=body, headers=auth_header(user))


@pytest.mark.parametrize("fields", [
    {"payment_date": TOMORROW},
    {"payment_date": "2016-03-31"},  # before GST
    {"amount": 0},
    {"gst_component": 600},  # more than the amount
    {"tds_deducted": -5},
    {"bank_reference": "AB"},
    {"bank_name": ""},
])
def test_receipt_rejects_bad_input(client, users, client_row, fields):
    inv = issued_invoice(client, users, client_row)
    assert receipt(client, users["exec"], inv["id"], **fields).status_code == 422


def test_receipt_cannot_predate_the_invoice(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    res = receipt(client, users["exec"], inv["id"], payment_date=(TODAY - timedelta(days=1)).isoformat())
    assert res.status_code == 400
    assert "before the invoice date" in res.json()["detail"]


def test_receipt_reference_is_upper_cased(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    res = receipt(client, users["exec"], inv["id"], bank_reference="hdfcr52026101200001234")
    assert res.status_code == 201, res.text
    assert res.json()["payments"][0]["bank_reference"] == "HDFCR52026101200001234"


# ---------- vendor bills ----------


@pytest.fixture
def vendor_id(client, users):
    body = {"business_name": "Quanta", "email": "a@quanta.example", "gst_number": GSTIN}
    return client.post("/api/vendors", json=body, headers=auth_header(users["exec"])).json()["id"]


@pytest.mark.parametrize("fields", [
    {"invoice_number": "BILL NUMBER 2026 OCT"},  # spaces and over 16 characters
    {"invoice_number": "B#1"},
    {"invoice_date": TOMORROW},
    {"invoice_date": "2017-06-30"},
    {"invoice_amount": 0},
    {"gst_rate": 15},
])
def test_vendor_bill_rejects_bad_input(client, users, vendor_id, fields):
    body = {"vendor_id": vendor_id, "invoice_number": "QR/26-27/001", "invoice_date": TODAY.isoformat(),
            "invoice_amount": 10000, **fields}
    assert client.post("/api/vendors/invoices", json=body, headers=auth_header(users["exec"])).status_code == 422


# ---------- payment approvals ----------


@pytest.mark.parametrize("fields", [
    {"amount": 0},
    {"tax_deductions": 2000},
    {"net_payable": 2000},
    {"payee_name": ""},
])
def test_payment_request_rejects_bad_amounts(client, users, fields):
    body = {"payee_name": "Quanta", "amount": 1000, **fields}
    assert client.post("/api/approvals", json=body, headers=auth_header(users["manager"])).status_code == 422


def test_rejecting_a_payment_needs_a_reason(client, users):
    approval = client.post("/api/approvals", json={"payee_name": "Quanta", "amount": 1000},
                           headers=auth_header(users["manager"])).json()
    res = client.post(f"/api/approvals/{approval['id']}/ceo", json={"approve": False},
                      headers=auth_header(users["ceo"]))
    assert res.status_code == 422
    assert "reason" in res.text


# ---------- bank statements ----------


def test_statement_upload_checks_the_account_number(client, users):
    res = client.post(
        "/api/verification/statements",
        files={"file": ("s.csv", b"date,amount\n", "text/csv")},
        data={"bank_name": "HDFC", "account_number": "12AB"},
        headers=auth_header(users["manager"]),
    )
    assert res.status_code == 422
    assert "Account number" in res.json()["detail"]
