"""Vendor onboarding: MSME, COI, PAN and any number of bank accounts."""

from tests.conftest import auth_header

HDFC = {"account_holder": "Quanta Research LLP", "bank_name": "HDFC Bank", "account_number": "50100123456789", "ifsc_code": "hdfc0001234"}
ICICI = {"account_holder": "Quanta Research LLP", "bank_name": "ICICI Bank", "account_number": "002701555888", "ifsc_code": "ICIC0000027"}


def vendor_body(**overrides):
    return {
        "business_name": "Quanta Research",
        "email": "accounts@quanta.example",
        "phone": "+91 98100 00000",
        "gst_number": "27AAGFQ1234C1Z5",
        "msme_number": "udyam-mh-26-0012345",
        "coi": "U72900MH2015PTC123456",
        "pan": "aagfq1234c",
        "bank_accounts": [HDFC, ICICI],
        **overrides,
    }


def create(client, user, **overrides):
    return client.post("/api/vendors", json=vendor_body(**overrides), headers=auth_header(user))


def test_vendor_keeps_msme_coi_pan_and_every_bank_account(client, users):
    res = create(client, users["exec"])
    assert res.status_code == 201, res.text
    v = res.json()
    assert v["msme_number"] == "UDYAM-MH-26-0012345"
    assert v["coi"] == "U72900MH2015PTC123456"
    assert v["pan"] == "AAGFQ1234C"
    assert [a["bank_name"] for a in v["bank_accounts"]] == ["HDFC Bank", "ICICI Bank"]
    assert v["bank_accounts"][0]["ifsc_code"] == "HDFC0001234"


def test_msme_and_coi_are_optional(client, users):
    res = create(client, users["exec"], msme_number="", coi=None)
    assert res.status_code == 201, res.text
    assert res.json()["msme_number"] is None
    assert res.json()["coi"] is None


def test_badly_formed_identifiers_are_rejected(client, users):
    assert create(client, users["exec"], pan="ABC123").status_code == 422
    assert create(client, users["exec"], msme_number="12345").status_code == 422
    bad_ifsc = {**HDFC, "ifsc_code": "HDFC1234"}
    assert create(client, users["exec"], bank_accounts=[bad_ifsc]).status_code == 422
    incomplete = {**HDFC, "account_number": ""}
    assert create(client, users["exec"], bank_accounts=[incomplete]).status_code == 422


def test_editing_bank_accounts_replaces_the_list(client, users):
    v = create(client, users["exec"]).json()
    res = client.patch(f"/api/vendors/{v['id']}", json={"bank_accounts": [ICICI]}, headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    assert [a["bank_name"] for a in res.json()["bank_accounts"]] == ["ICICI Bank"]

    # Leaving bank_accounts out of an edit keeps them.
    res = client.patch(f"/api/vendors/{v['id']}", json={"phone": "+91 90000 00000"}, headers=auth_header(users["manager"]))
    assert [a["bank_name"] for a in res.json()["bank_accounts"]] == ["ICICI Bank"]


def test_verification_needs_a_bank_account(client, users):
    v = create(client, users["exec"], bank_accounts=[]).json()
    res = client.post(f"/api/vendors/{v['id']}/verify", headers=auth_header(users["manager"]))
    assert res.status_code == 400
    assert "bank account" in res.json()["detail"]

    no_tax_ids = create(client, users["exec"], gst_number="", pan="").json()
    res = client.post(f"/api/vendors/{no_tax_ids['id']}/verify", headers=auth_header(users["manager"]))
    assert res.json()["detail"] == "Cannot verify - missing mandatory details: GST number, PAN"

    client.patch(f"/api/vendors/{v['id']}", json={"bank_accounts": [HDFC]}, headers=auth_header(users["manager"]))
    res = client.post(f"/api/vendors/{v['id']}/verify", headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    assert res.json()["is_verified"] is True


def test_erase_covers_vendor_bank_accounts():
    from app.services.wipe import DELETE_ORDER

    assert DELETE_ORDER.index("vendor_bank_accounts") < DELETE_ORDER.index("vendors")
