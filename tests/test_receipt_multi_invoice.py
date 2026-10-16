"""One bank receipt can pay several invoices of the same client, split as the team chooses."""

from app.models import BankTransaction, Client, ClientInvoice, Payment
from app.services.agents import house_agent
from app.services.numbering import business_date
from app.services.reconciliation import auto_match
from tests.conftest import auth_header, project_id_for

# 1,000 taxable + 18% GST = 1,180; the client deducts 10% TDS (100), so 1,080 clears an invoice.
NET_OF_TDS = 1080


def issued_invoice(client, users, client_row):
    inv = client.post(
        "/api/invoices",
        json={"project_id": project_id_for(client_row), "taxable_value": 1000, "tds_rate": 10},
        headers=auth_header(users["exec"]),
    ).json()
    sent = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["coordinator"])).json()
    client.post(f"/api/public/invoices/{sent['link_url'].rsplit('/', 1)[-1]}/approve", json={"name": "A. Client"})
    res = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


def receipt(client, user, allocations, **overrides):
    body = {
        "payment_date": business_date().isoformat(),
        "bank_name": "HDFC Bank",
        "bank_reference": "UTR998877",
        "allocations": allocations,
        **overrides,
    }
    return client.post("/api/invoices/receipts", json=body, headers=auth_header(user))


def test_one_receipt_pays_two_invoices_and_they_share_a_reference(client, db, users, client_row):
    first, second = issued_invoice(client, users, client_row), issued_invoice(client, users, client_row)
    res = receipt(
        client, users["exec"],
        [{"invoice_id": first["id"], "amount": NET_OF_TDS}, {"invoice_id": second["id"], "amount": NET_OF_TDS}],
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["total"] == 2 * NET_OF_TDS
    assert {p["receipt_ref"] for p in body["payments"]} == {body["receipt_ref"]}
    for inv in (first, second):
        row = db.get(ClientInvoice, inv["id"])
        db.refresh(row)
        assert row.status.value == "Fully Paid"
        assert row.payments[0].tds_deducted == 100
        assert row.payments[0].bank_reference == "UTR998877"


def test_a_part_payment_can_be_split_unevenly(client, db, users, client_row):
    first, second = issued_invoice(client, users, client_row), issued_invoice(client, users, client_row)
    res = receipt(client, users["exec"], [{"invoice_id": first["id"], "amount": NET_OF_TDS}, {"invoice_id": second["id"], "amount": 300}])
    assert res.status_code == 201, res.text
    row = db.get(ClientInvoice, second["id"])
    db.refresh(row)
    assert row.status.value == "Partially Paid"
    assert row.payments[0].tds_deducted == 0  # a part payment counts no TDS yet


def test_too_much_on_one_invoice_records_nothing_and_names_it(client, db, users, client_row):
    first, second = issued_invoice(client, users, client_row), issued_invoice(client, users, client_row)
    res = receipt(client, users["exec"], [{"invoice_id": first["id"], "amount": NET_OF_TDS}, {"invoice_id": second["id"], "amount": 5000}])
    assert res.status_code == 400
    assert second["display_number"] in res.json()["detail"]
    assert db.query(Payment).count() == 0


def test_invoices_of_different_clients_cannot_share_a_receipt(client, db, users, client_row):
    first = issued_invoice(client, users, client_row)
    other = Client(business_name="Bharat Steel", email="ap@bharat.example", gst_number="29CCCCC2222C1Z5",
                   agent_id=house_agent(db).id)
    db.add(other)
    db.commit()
    second = issued_invoice(client, users, other)
    res = receipt(client, users["exec"], [{"invoice_id": first["id"], "amount": 100}, {"invoice_id": second["id"], "amount": 100}])
    assert res.status_code == 400
    assert "same client" in res.json()["detail"]


def test_an_invoice_appears_once_and_a_bank_is_required(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    twice = [{"invoice_id": inv["id"], "amount": 100}, {"invoice_id": inv["id"], "amount": 100}]
    assert receipt(client, users["exec"], twice).status_code == 422
    assert receipt(client, users["exec"], [{"invoice_id": inv["id"], "amount": 100}], bank_name=None).status_code == 422
    assert receipt(client, users["exec"], []).status_code == 422


def test_a_bank_line_for_the_whole_receipt_matches_it(client, db, users, client_row):
    first, second = issued_invoice(client, users, client_row), issued_invoice(client, users, client_row)
    receipt(
        client, users["exec"],
        [{"invoice_id": first["id"], "amount": NET_OF_TDS}, {"invoice_id": second["id"], "amount": 500}],
        bank_reference=None,
    )
    txn = BankTransaction(amount=NET_OF_TDS + 500, utr_reference=None)
    assert auto_match(db, txn) is True
    assert "across 2 invoices" in txn.match_note
