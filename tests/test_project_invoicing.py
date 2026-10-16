"""Invoices are raised against a project; the project carries the client, service and contract value."""

import pytest

from app.models import ClientInvoice, InvoiceStatus, Project, Service
from tests.conftest import auth_header, default_service_id, project_id_for


@pytest.fixture
def seo(db):
    service = Service(title="SEO retainer", sac_code="998365", gst_rate=18.0)
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def new_project(client, user, client_row, service_id, **fields):
    body = {"code": "ACME-SEO", "title": "Acme SEO", "client_id": client_row.id, "service_id": service_id, **fields}
    return client.post("/api/projects", json=body, headers=auth_header(user))


def new_invoice(client, user, project_id, **fields):
    body = {"project_id": project_id, "taxable_value": 1000, **fields}
    return client.post("/api/invoices", json=body, headers=auth_header(user))


# ---------- projects ----------


def test_project_needs_an_active_service(client, users, client_row, seo, db):
    assert new_project(client, users["manager"], client_row, None).status_code == 422
    seo.is_active = False
    db.commit()
    res = new_project(client, users["manager"], client_row, seo.id)
    assert res.status_code == 400
    assert "deactivated" in res.json()["detail"]


def test_project_shows_its_service_and_billing(client, users, client_row, seo, db):
    project = new_project(client, users["manager"], client_row, seo.id, contract_value=5000).json()
    assert (project["service_title"], project["sac_code"], project["gst_rate"]) == ("SEO retainer", "998365", 18.0)

    first = new_invoice(client, users["exec"], project["id"], taxable_value=2000).json()
    new_invoice(client, users["exec"], project["id"], taxable_value=4000)
    cancelled = new_invoice(client, users["exec"], project["id"], taxable_value=9000).json()
    db.get(ClientInvoice, cancelled["id"]).status = InvoiceStatus.CANCELLED
    db.commit()

    detail = client.get(f"/api/projects/{project['id']}", headers=auth_header(users["exec"])).json()
    assert detail["billed"] == 6000  # the cancelled invoice does not count
    assert detail["left_to_bill"] == -1000
    assert detail["over_contract"] is True
    assert detail["margin"] == 6000  # no vendor cost yet
    assert first["id"] in [i["id"] for i in detail["client_invoices"]]


def test_left_to_bill_is_unknown_without_a_contract_value(client, users, client_row, seo):
    project = new_project(client, users["manager"], client_row, seo.id).json()
    assert project["left_to_bill"] is None
    assert project["over_contract"] is False


def test_service_on_a_project_cannot_be_deleted(client, users, client_row, seo):
    new_project(client, users["manager"], client_row, seo.id)
    res = client.delete(f"/api/services/{seo.id}", headers=auth_header(users["manager"]))
    assert res.status_code == 409


def test_project_with_client_invoices_cannot_be_deleted(client, users, client_row, seo):
    project = new_project(client, users["manager"], client_row, seo.id).json()
    new_invoice(client, users["exec"], project["id"])
    res = client.delete(f"/api/projects/{project['id']}", headers=auth_header(users["manager"]))
    assert res.status_code == 409


# ---------- invoices ----------


def test_invoice_needs_a_project(client, users, client_row):
    body = {"client_id": client_row.id, "service_id": default_service_id(), "taxable_value": 1000}
    assert client.post("/api/invoices", json=body, headers=auth_header(users["exec"])).status_code == 422


def test_invoice_takes_client_and_service_from_the_project(client, users, client_row, seo, db):
    project = new_project(client, users["manager"], client_row, seo.id).json()
    res = new_invoice(client, users["exec"], project["id"], gst_rate=5, sac_code="998311", client_id=999)
    assert res.status_code == 201, res.text
    inv = res.json()
    assert inv["project_id"] == project["id"]
    assert inv["client_id"] == client_row.id
    assert (inv["service_id"], inv["sac_code"], inv["gst_rate"]) == (seo.id, "998365", 18.0)


def test_project_without_a_service_cannot_be_invoiced(client, users, client_row, db):
    legacy = Project(code="OLD-1", title="Old work", client_id=client_row.id)
    db.add(legacy)
    db.commit()
    res = new_invoice(client, users["exec"], legacy.id)
    assert res.status_code == 400
    assert "no service" in res.json()["detail"]


def test_changing_the_project_on_a_draft_follows_the_new_project(client, users, client_row, seo):
    inv = new_invoice(client, users["exec"], project_id_for(client_row)).json()
    project = new_project(client, users["manager"], client_row, seo.id).json()
    res = client.patch(f"/api/invoices/{inv['id']}", json={"project_id": project["id"]}, headers=auth_header(users["exec"]))
    assert res.status_code == 200, res.text
    assert (res.json()["service_id"], res.json()["sac_code"]) == (seo.id, "998365")


def test_issued_invoice_keeps_its_service_when_the_project_changes(client, users, client_row, seo, db):
    project_id = project_id_for(client_row)
    inv = new_invoice(client, users["exec"], project_id).json()
    sent = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["manager"])).json()
    client.post(f"/api/public/invoices/{sent['link_url'].rsplit('/', 1)[-1]}/approve", json={"name": "A. Client"})
    issued = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"])).json()["invoice"]

    client.patch(f"/api/projects/{project_id}", json={"service_id": seo.id}, headers=auth_header(users["manager"]))
    again = client.get(f"/api/invoices/{inv['id']}", headers=auth_header(users["exec"])).json()
    assert (again["service_id"], again["sac_code"]) == (issued["service_id"], issued["sac_code"])


def test_duplicate_keeps_the_project(client, users, client_row):
    inv = new_invoice(client, users["exec"], project_id_for(client_row)).json()
    copy = client.post(f"/api/invoices/{inv['id']}/duplicate", headers=auth_header(users["exec"])).json()
    assert copy["project_id"] == inv["project_id"]
