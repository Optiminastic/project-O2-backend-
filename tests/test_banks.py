"""Our bank accounts: added by name and the last four digits, then picked on each receipt."""

from app.services.numbering import business_date
from tests.conftest import auth_header
from tests.test_payment_bank import issued_invoice


def add_bank(client, user, **overrides):
    body = {"name": "IndusInd Bank", "account_last4": "2018", **overrides}
    return client.post("/api/banks", json=body, headers=auth_header(user))


def receive(client, user, invoice_id, **fields):
    body = {"amount": 500, "payment_date": business_date().isoformat(), **fields}
    return client.post(f"/api/invoices/{invoice_id}/payments", json=body, headers=auth_header(user))


def test_manager_adds_a_bank_by_name_and_last_four_digits(client, users):
    res = add_bank(client, users["manager"])
    assert res.status_code == 201, res.text
    bank = res.json()
    assert (bank["name"], bank["account_last4"], bank["label"]) == ("IndusInd Bank", "2018", "IndusInd Bank ··2018")
    listed = client.get("/api/banks", headers=auth_header(users["exec"])).json()
    assert [b["label"] for b in listed] == ["IndusInd Bank ··2018"]


def test_only_four_digits_are_kept(client, users):
    for last4 in ("201", "20188", "20a8", "252122052018"):
        assert add_bank(client, users["manager"], account_last4=last4).status_code == 422
    assert add_bank(client, users["manager"], name=" ").status_code == 422


def test_executive_cannot_add_a_bank(client, users):
    assert add_bank(client, users["exec"]).status_code == 403


def test_the_same_account_cannot_be_added_twice(client, users):
    assert add_bank(client, users["manager"]).status_code == 201
    assert add_bank(client, users["manager"], name="indusind bank").status_code == 409
    assert add_bank(client, users["manager"], account_last4="1075").status_code == 201


def test_receipt_records_the_chosen_bank(client, users, client_row):
    bank = add_bank(client, users["manager"]).json()
    inv = issued_invoice(client, users, client_row)
    res = receive(client, users["exec"], inv["id"], bank_account_id=bank["id"], bank_reference="UTR123456")
    assert res.status_code == 201, res.text
    payment = res.json()["payments"][0]
    assert (payment["bank_account_id"], payment["bank_name"]) == (bank["id"], "IndusInd Bank ··2018")


def test_receipt_needs_a_bank(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    assert receive(client, users["exec"], inv["id"]).status_code == 422


def test_deactivated_bank_is_hidden_and_refused(client, users, client_row):
    bank = add_bank(client, users["manager"]).json()
    client.patch(f"/api/banks/{bank['id']}", json={"is_active": False}, headers=auth_header(users["manager"]))
    assert client.get("/api/banks", headers=auth_header(users["exec"])).json() == []
    everything = client.get("/api/banks?include_inactive=true", headers=auth_header(users["manager"])).json()
    assert [b["is_active"] for b in everything] == [False]
    inv = issued_invoice(client, users, client_row)
    assert receive(client, users["exec"], inv["id"], bank_account_id=bank["id"]).status_code == 400


def test_bank_with_receipts_cannot_be_deleted(client, users, client_row):
    bank = add_bank(client, users["manager"]).json()
    unused = add_bank(client, users["manager"], account_last4="1075").json()
    inv = issued_invoice(client, users, client_row)
    receive(client, users["exec"], inv["id"], bank_account_id=bank["id"])
    assert client.delete(f"/api/banks/{bank['id']}", headers=auth_header(users["manager"])).status_code == 409
    assert client.delete(f"/api/banks/{unused['id']}", headers=auth_header(users["manager"])).status_code == 204
