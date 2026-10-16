"""A proforma is due at month end by default; a tax invoice by the 15th of the next month."""

from datetime import date

from app.models import ClientInvoice
from app.services.invoice_workflow import payment_due_date, tax_invoice_date
from app.services.numbering import business_date, month_end
from tests.conftest import auth_header, project_id_for


def test_month_end_handles_month_lengths_and_leap_years():
    assert month_end(date(2026, 10, 6)) == date(2026, 10, 31)
    assert month_end(date(2026, 11, 1)) == date(2026, 11, 30)
    assert month_end(date(2027, 2, 10)) == date(2027, 2, 28)
    assert month_end(date(2028, 2, 10)) == date(2028, 2, 29)
    assert month_end(date(2026, 12, 31)) == date(2026, 12, 31)


def create(client, user, client_row, **overrides):
    body = {"project_id": project_id_for(client_row), "taxable_value": 1000, **overrides}
    res = client.post("/api/invoices", json=body, headers=auth_header(user))
    assert res.status_code == 201, res.text
    return res.json()


def test_new_proforma_is_due_at_month_end(client, users, client_row):
    inv = create(client, users["exec"], client_row)
    assert inv["due_date"] == month_end(business_date()).isoformat()


def test_explicit_due_date_is_kept(client, users, client_row):
    inv = create(client, users["exec"], client_row, due_date="2099-01-15")
    assert inv["due_date"] == "2099-01-15"


def test_duplicate_gets_a_fresh_month_end_due_date(client, users, client_row, db):
    src = create(client, users["exec"], client_row)
    # A proforma whose due date has long passed; the API no longer accepts one directly.
    db.get(ClientInvoice, src["id"]).due_date = date(2020, 1, 15)
    db.commit()
    res = client.post(f"/api/invoices/{src['id']}/duplicate", headers=auth_header(users["exec"]))
    assert res.status_code == 201, res.text
    assert res.json()["due_date"] == month_end(business_date()).isoformat()


def _issue(client, users, inv):
    sent = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["coordinator"])).json()
    client.post(f"/api/public/invoices/{sent['link_url'].rsplit('/', 1)[-1]}/approve", json={"name": "A. Client"})
    res = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


def test_tax_invoice_is_due_on_the_15th_of_the_next_month(client, users, client_row):
    inv = create(client, users["exec"], client_row, due_date="2099-01-15")
    issued = _issue(client, users, inv)
    assert issued["due_date"] == payment_due_date(business_date()).isoformat()


def test_tax_invoice_takes_a_backdated_proforma_date_from_this_month(client, users, client_row, db):
    inv = create(client, users["exec"], client_row)
    first = business_date().replace(day=1)
    row = db.get(ClientInvoice, inv["id"])
    row.proforma_date = first
    db.commit()
    issued = _issue(client, users, inv)
    assert issued["invoice_date"] == first.isoformat()
    assert issued["due_date"] == payment_due_date(first).isoformat()


def test_proforma_from_an_earlier_month_is_issued_today(client, users, client_row, db):
    inv = create(client, users["exec"], client_row)
    row = db.get(ClientInvoice, inv["id"])
    row.proforma_date = date(2020, 1, 31)
    row.due_date = date(2020, 1, 31)
    db.commit()
    issued = _issue(client, users, inv)
    assert issued["invoice_date"] == business_date().isoformat()


def test_payment_due_date_rolls_over_the_year():
    assert payment_due_date(date(2026, 12, 3)) == date(2027, 1, 15)
    assert payment_due_date(date(2026, 10, 31)) == date(2026, 11, 15)


def test_tax_invoice_date_rule():
    today = date(2026, 10, 16)
    assert tax_invoice_date(date(2026, 10, 2), today) == date(2026, 10, 2)
    assert tax_invoice_date(date(2026, 9, 30), today) == today
    assert tax_invoice_date(None, today) == today
