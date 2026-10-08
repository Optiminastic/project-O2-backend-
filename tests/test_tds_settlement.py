"""A client that deducts TDS settles the invoice with cash plus the TDS it deducted."""

from tests.conftest import auth_header
from tests.test_payment_bank import issued_invoice  # 1,000 taxable + 18% GST = 1,180; TDS 10% = 100


def pay(client, user, invoice_id, amount, tds=0.0):
    body = {"amount": amount, "payment_date": "2026-10-06", "bank_name": "HDFC Bank", "tds_deducted": tds}
    return client.post(f"/api/invoices/{invoice_id}/payments", json=body, headers=auth_header(user))


def test_paying_total_minus_tds_settles_the_invoice(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    res = pay(client, users["exec"], inv["id"], 1080, tds=100)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "Fully Paid"
    assert body["amount_pending"] == 0
    assert body["amount_received"] == 1080  # cash only; the TDS is a tax credit, not cash


def test_part_payment_with_tds_leaves_the_rest_pending(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    body = pay(client, users["exec"], inv["id"], 500, tds=50).json()
    assert body["status"] == "Partially Paid"
    assert body["amount_pending"] == 630
    body = pay(client, users["exec"], inv["id"], 580, tds=50).json()
    assert body["status"] == "Fully Paid"


def test_cash_plus_tds_cannot_exceed_what_is_due(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    res = pay(client, users["exec"], inv["id"], 1180, tds=100)
    assert res.status_code == 400
    assert "1,180" in res.json()["detail"] or "1180" in res.json()["detail"]


def test_tds_cannot_exceed_the_expected_deduction(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    res = pay(client, users["exec"], inv["id"], 1000, tds=180)
    assert res.status_code == 400
    assert "TDS" in res.json()["detail"]


def test_negative_tds_is_rejected(client, users, client_row):
    inv = issued_invoice(client, users, client_row)
    assert pay(client, users["exec"], inv["id"], 500, tds=-10).status_code in (400, 422)


def test_invoices_stuck_on_tds_are_settled_by_the_upgrade(db, client_row):
    from datetime import date

    from app.database import engine
    from app.models import ClientInvoice, InvoiceStatus, Payment
    from app.schema_upgrades import TAX_TRACKING, _run
    from tests.conftest import default_service_id

    stuck = ClientInvoice(
        client_id=client_row.id, agent_id=client_row.agent_id, service_id=default_service_id(),
        invoice_number="INV/OLD/1", invoice_date=date(2026, 9, 1), status=InvoiceStatus.PARTIALLY_PAID,
        taxable_value=1000, gst_amount=180, total_amount=1180, amount_received=1080, amount_pending=100,
        tds_rate=10, expected_tds=100,
    )
    db.add(stuck)
    db.flush()
    db.add(Payment(invoice_id=stuck.id, amount=1080, tds_deducted=100, payment_date=date(2026, 9, 5), bank_name="HDFC"))
    db.commit()

    _run(engine, TAX_TRACKING)
    _run(engine, TAX_TRACKING)  # idempotent

    db.expire_all()
    fixed = db.get(ClientInvoice, stuck.id)
    assert (fixed.status, fixed.amount_pending) == (InvoiceStatus.FULLY_PAID, 0)
