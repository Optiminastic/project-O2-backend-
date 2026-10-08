"""TDS always applies to client invoices; the default rate is 10% (section 194J professional fees)."""

from tests.conftest import auth_header, default_service_id


def create(client, user, client_row, **overrides):
    body = {"client_id": client_row.id, "taxable_value": 100000, "service_id": default_service_id(), **overrides}
    res = client.post("/api/invoices", json=body, headers=auth_header(user))
    assert res.status_code == 201, res.text
    return res.json()


def test_tds_applies_at_ten_percent_by_default(client, users, client_row):
    inv = create(client, users["exec"], client_row)
    assert inv["tds_applicable"] is True
    assert inv["tds_rate"] == 10
    assert inv["expected_tds"] == 10000


def test_rate_can_be_changed_per_invoice(client, users, client_row):
    inv = create(client, users["exec"], client_row, tds_rate=2)
    assert inv["tds_rate"] == 2
    assert inv["expected_tds"] == 2000


def test_tds_cannot_be_switched_off(client, users, client_row):
    inv = create(client, users["exec"], client_row, tds_applicable=False)
    assert inv["tds_applicable"] is True
    res = client.patch(f"/api/invoices/{inv['id']}", json={"tds_applicable": False}, headers=auth_header(users["exec"]))
    assert res.json()["tds_applicable"] is True
