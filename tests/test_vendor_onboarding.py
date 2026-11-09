"""Vendor onboarding as the PROJECT O2 sheet lays it out: GSTIN, address, MSME Yes/No, optional COI, bank accounts."""

import pytest

from tests.conftest import VENDOR_HDFC, auth_header, vendor_body

HDFC = VENDOR_HDFC
ICICI = {"account_holder": "Quanta Research LLP", "bank_name": "ICICI Bank", "account_number": "002701555888", "ifsc_code": "ICIC0000027"}



def create(client, user, **overrides):
    return client.post("/api/vendors", json=vendor_body(**{"bank_accounts": [HDFC, ICICI], **overrides}), headers=auth_header(user))


def test_vendor_keeps_msme_coi_and_every_bank_account(client, users):
    res = create(client, users["exec"])
    assert res.status_code == 201, res.text
    v = res.json()
    assert (v["msme"], v["msme_number"]) == (True, "UDYAM-MH-26-0012345")
    assert v["coi"] == "U72900MH2015PTC123456"
    assert v["address"].startswith("4th Floor")
    assert [a["bank_name"] for a in v["bank_accounts"]] == ["HDFC Bank", "ICICI Bank"]
    assert v["bank_accounts"][0]["ifsc_code"] == "HDFC0001234"


def test_pan_is_taken_from_the_gstin(client, users):
    assert create(client, users["exec"]).json()["pan"] == "AAGFQ1234C"


def test_udyam_number_and_coi_are_optional(client, users):
    res = create(client, users["exec"], msme_number="", coi="")
    assert res.status_code == 201, res.text
    assert (res.json()["msme"], res.json()["msme_number"], res.json()["coi"]) == (True, None, None)


def test_a_vendor_that_is_not_an_msme_keeps_no_udyam_number(client, users):
    res = create(client, users["exec"], msme=False)
    assert res.status_code == 201, res.text
    assert (res.json()["msme"], res.json()["msme_number"]) == (False, None)


@pytest.mark.parametrize("missing", ["gst_number", "address", "msme"])
def test_the_sheets_vendor_fields_are_required(client, users, missing):
    body = vendor_body()
    del body[missing]
    assert client.post("/api/vendors", json=body, headers=auth_header(users["exec"])).status_code == 422
    if missing != "msme":
        assert create(client, users["exec"], **{missing: ""}).status_code == 422


def test_a_vendor_needs_a_bank_account(client, users):
    body = {**vendor_body(), "bank_accounts": []}
    assert client.post("/api/vendors", json=body, headers=auth_header(users["exec"])).status_code == 422


def test_an_edit_cannot_blank_a_required_field(client, users):
    v = create(client, users["exec"]).json()
    url = f"/api/vendors/{v['id']}"
    for change in ({"gst_number": ""}, {"address": ""}, {"bank_accounts": []}):
        assert client.patch(url, json=change, headers=auth_header(users["manager"])).status_code == 422, change
    res = client.patch(url, json={"msme": False}, headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    assert (res.json()["msme"], res.json()["msme_number"]) == (False, None)


def test_badly_formed_identifiers_are_rejected(client, users):
    assert create(client, users["exec"], pan="ABC123").status_code == 422
    assert create(client, users["exec"], gst_number="27AAGFQ1234C1ZX").status_code == 422
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


def test_a_complete_vendor_can_be_verified(client, users):
    v = create(client, users["exec"]).json()
    res = client.post(f"/api/vendors/{v['id']}/verify", headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    assert res.json()["is_verified"] is True
