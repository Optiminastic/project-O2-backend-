"""Invoices are due at the end of the month by default."""

from datetime import date

from app.models import ClientInvoice
from app.services.numbering import business_date, month_end
from tests.conftest import auth_header, default_service_id


def test_month_end_handles_month_lengths_and_leap_years():
    assert month_end(date(2026, 10, 6)) == date(2026, 10, 31)
    assert month_end(date(2026, 11, 1)) == date(2026, 11, 30)
    assert month_end(date(2027, 2, 10)) == date(2027, 2, 28)
    assert month_end(date(2028, 2, 10)) == date(2028, 2, 29)
    assert month_end(date(2026, 12, 31)) == date(2026, 12, 31)


def create(client, user, client_row, **overrides):
    body = {"client_id": client_row.id, "taxable_value": 1000, "service_id": default_service_id(), **overrides}
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
    src = create(client, users["exec"], client_row, due_date="2020-01-15")
    res = client.post(f"/api/invoices/{src['id']}/duplicate", headers=auth_header(users["exec"]))
    assert res.status_code == 201, res.text
    assert res.json()["due_date"] == month_end(business_date()).isoformat()


def test_issue_moves_a_past_due_date_to_the_end_of_the_issue_month(client, users, client_row, db):
    inv = create(client, users["exec"], client_row)
    # Simulate a proforma raised last month whose month-end has already passed.
    row = db.get(ClientInvoice, inv["id"])
    row.due_date = date(2020, 1, 31)
    db.commit()

    sent = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["exec"])).json()
    client.post(f"/api/public/invoices/{sent['link_url'].rsplit('/', 1)[-1]}/approve", json={"name": "A. Client"})
    issued = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"])).json()["invoice"]
    assert issued["due_date"] == month_end(business_date()).isoformat()


def test_issue_keeps_a_future_due_date(client, users, client_row):
    inv = create(client, users["exec"], client_row, due_date="2099-01-15")
    sent = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["exec"])).json()
    client.post(f"/api/public/invoices/{sent['link_url'].rsplit('/', 1)[-1]}/approve", json={"name": "A. Client"})
    issued = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"])).json()["invoice"]
    assert issued["due_date"] == "2099-01-15"
