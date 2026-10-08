"""Client payments are received by bank: mode is always Bank and the bank name is required."""

from tests.conftest import auth_header, default_service_id


def issued_invoice(client, users, client_row):
    inv = client.post(
        "/api/invoices",
        json={"client_id": client_row.id, "taxable_value": 1000, "service_id": default_service_id()},
        headers=auth_header(users["exec"]),
    ).json()
    sent = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["exec"])).json()
    client.post(f"/api/public/invoices/{sent['link_url'].rsplit('/', 1)[-1]}/approve", json={"name": "A. Client"})
    client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    return inv


def pay(client, user, invoice_id, **overrides):
    body = {"amount": 500, "payment_date": "2026-10-06", "bank_reference": "UTR123", **overrides}
    return client.post(f"/api/invoices/{invoice_id}/payments", json=body, headers=auth_header(user))


def test_payment_is_recorded_as_bank_with_its_bank_name(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    res = pay(client, users["exec"], inv["id"], bank_name="HDFC Bank")
    assert res.status_code == 201, res.text
    payment = res.json()["payments"][0]
    assert payment["payment_mode"] == "Bank"
    assert payment["bank_name"] == "HDFC Bank"


def test_bank_name_is_required(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    assert pay(client, users["exec"], inv["id"]).status_code == 422
    assert pay(client, users["exec"], inv["id"], bank_name="  ").status_code == 422


def test_other_modes_are_not_accepted_any_more(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    res = pay(client, users["exec"], inv["id"], bank_name="HDFC Bank", payment_mode="Cash")
    assert res.status_code == 201, res.text
    assert res.json()["payments"][0]["payment_mode"] == "Bank"


def test_receipts_ledger_shows_the_bank(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    pay(client, users["exec"], inv["id"], bank_name="ICICI Bank")
    rows = client.get("/api/taxation/receipts", headers=auth_header(users["manager"])).json()
    assert rows[0]["bank_name"] == "ICICI Bank"
    assert rows[0]["payment_mode"] == "Bank"
