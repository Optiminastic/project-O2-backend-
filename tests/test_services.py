"""Service catalogue: every invoice is for one service, which pre-fills its tax details."""

from app.models import ClientInvoice
from tests.conftest import auth_header, default_service_id


def add_service(client, user, **overrides):
    body = {
        "title": "Statutory audit",
        "description": "Annual statutory audit under the Companies Act",
        "sac_code": "998221",
        "gst_rate": 18,
        **overrides,
    }
    return client.post("/api/services", json=body, headers=auth_header(user))


def new_invoice(client, user, client_row, **overrides):
    body = {"client_id": client_row.id, "taxable_value": 1000, "service_id": default_service_id(), **overrides}
    return client.post("/api/invoices", json=body, headers=auth_header(user))


# ---------- Catalogue management ----------


def test_manager_adds_a_service(client, users):
    res = add_service(client, users["manager"])
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["title"] == "Statutory audit"
    assert body["sac_code"] == "998221"
    assert body["gst_rate"] == 18
    assert body["is_active"] is True


def test_executive_cannot_change_the_catalogue(client, users):
    assert add_service(client, users["exec"]).status_code == 403


def test_service_needs_a_title_and_a_valid_gst_rate(client, users):
    assert add_service(client, users["manager"], title="  ").status_code == 422
    assert add_service(client, users["manager"], gst_rate=-1).status_code == 422
    assert add_service(client, users["manager"], gst_rate=41).status_code == 422


def test_service_titles_are_unique(client, users):
    assert add_service(client, users["manager"]).status_code == 201
    assert add_service(client, users["manager"], title="statutory AUDIT").status_code == 409


def test_everyone_lists_active_services_only(client, users):
    created = add_service(client, users["manager"]).json()
    client.patch(f"/api/services/{created['id']}", json={"is_active": False}, headers=auth_header(users["manager"]))
    titles = [s["title"] for s in client.get("/api/services", headers=auth_header(users["exec"])).json()]
    assert "Statutory audit" not in titles
    all_titles = [
        s["title"]
        for s in client.get("/api/services?include_inactive=true", headers=auth_header(users["manager"])).json()
    ]
    assert "Statutory audit" in all_titles


# ---------- Invoices ----------


def test_invoice_requires_a_service(client, users, client_row):
    res = client.post(
        "/api/invoices", json={"client_id": client_row.id, "taxable_value": 1000}, headers=auth_header(users["exec"])
    )
    assert res.status_code == 422


def test_service_prefills_title_description_sac_and_gst_rate(client, users, client_row):
    svc = add_service(client, users["manager"], gst_rate=5).json()
    res = new_invoice(client, users["exec"], client_row, service_id=svc["id"])
    assert res.status_code == 201, res.text
    inv = res.json()
    assert inv["service_id"] == svc["id"]
    assert inv["service_title"] == "Statutory audit"
    assert inv["service_description"] == "Annual statutory audit under the Companies Act"
    assert inv["sac_code"] == "998221"
    assert inv["gst_rate"] == 5
    assert inv["gst_amount"] == 50  # 5% of 1000


def test_invoice_can_override_the_prefilled_details(client, users, client_row):
    svc = add_service(client, users["manager"]).json()
    inv = new_invoice(
        client, users["exec"], client_row, service_id=svc["id"], service_description="FY 2025-26 audit", gst_rate=12
    ).json()
    assert inv["service_description"] == "FY 2025-26 audit"
    assert inv["gst_rate"] == 12


def test_inactive_service_cannot_be_used(client, users, client_row):
    svc = add_service(client, users["manager"]).json()
    client.patch(f"/api/services/{svc['id']}", json={"is_active": False}, headers=auth_header(users["manager"]))
    assert new_invoice(client, users["exec"], client_row, service_id=svc["id"]).status_code == 400


def test_editing_the_catalogue_does_not_rewrite_existing_invoices(client, users, client_row):
    svc = add_service(client, users["manager"]).json()
    inv = new_invoice(client, users["exec"], client_row, service_id=svc["id"]).json()
    client.patch(
        f"/api/services/{svc['id']}",
        json={"title": "Audit (renamed)", "sac_code": "999999", "gst_rate": 28},
        headers=auth_header(users["manager"]),
    )
    again = client.get(f"/api/invoices/{inv['id']}", headers=auth_header(users["exec"])).json()
    assert again["service_title"] == "Statutory audit"
    assert again["sac_code"] == "998221"
    assert again["gst_rate"] == 18


def test_changing_a_proformas_service_reapplies_its_details(client, users, client_row):
    first = add_service(client, users["manager"]).json()
    second = add_service(client, users["manager"], title="Tax filing", description="ITR filing", sac_code="998231", gst_rate=18).json()
    inv = new_invoice(client, users["exec"], client_row, service_id=first["id"]).json()
    res = client.patch(f"/api/invoices/{inv['id']}", json={"service_id": second["id"]}, headers=auth_header(users["exec"]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["service_title"], body["service_description"], body["sac_code"]) == ("Tax filing", "ITR filing", "998231")


def test_issued_invoice_service_is_locked(client, users, client_row):
    inv = new_invoice(client, users["exec"], client_row).json()
    sent = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["exec"])).json()
    client.post(f"/api/public/invoices/{sent['link_url'].rsplit('/', 1)[-1]}/approve", json={"name": "A. Client"})
    client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    other = add_service(client, users["manager"]).json()
    res = client.patch(f"/api/invoices/{inv['id']}", json={"service_id": other["id"]}, headers=auth_header(users["manager"]))
    assert res.status_code == 423


def test_client_sees_the_service_title(client, users, client_row):
    inv = new_invoice(client, users["exec"], client_row).json()
    sent = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["exec"])).json()
    view = client.get(f"/api/public/invoices/{sent['link_url'].rsplit('/', 1)[-1]}").json()
    assert view["invoice"]["service_title"] == inv["service_title"]


# ---------- Deleting ----------


def test_unused_service_can_be_deleted(client, users):
    svc = add_service(client, users["manager"]).json()
    assert client.delete(f"/api/services/{svc['id']}", headers=auth_header(users["manager"])).status_code == 204


def test_service_used_on_an_invoice_must_be_deactivated_instead(client, users, client_row, db):
    svc = add_service(client, users["manager"]).json()
    new_invoice(client, users["exec"], client_row, service_id=svc["id"])
    res = client.delete(f"/api/services/{svc['id']}", headers=auth_header(users["manager"]))
    assert res.status_code == 409
    assert "deactivate" in res.json()["detail"].lower()
    assert db.query(ClientInvoice).count() == 1
