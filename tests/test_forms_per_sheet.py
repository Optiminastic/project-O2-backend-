"""The forms as the PROJECT O2 sheet lays them out: multi-service projects, invoice lines,
IGST by GSTIN state, backdating within the month, manager approval, automatic TDS and MSME clients.

Our banks (the Banks page) are tested in test_banks.py."""

from datetime import timedelta

import pytest

from app.models import Client, ClientInvoice, Project, Service
from app.services.agents import house_agent
from app.services.numbering import business_date
from tests.conftest import auth_header, default_service_id
from tests.test_payment_bank import issued_invoice


def add_service(db, title, sac_code, gst_rate=18.0) -> Service:
    service = Service(title=title, sac_code=sac_code, gst_rate=gst_rate)
    db.add(service)
    db.commit()
    return service


@pytest.fixture
def two_services(db):
    return add_service(db, "Media buying", "998366"), add_service(db, "Creative design", "998391")


def new_client(db, gst_number=None, name="Bharat Steel") -> Client:
    row = Client(business_name=name, email="ap@bharat.example", gst_number=gst_number, agent_id=house_agent(db).id)
    db.add(row)
    db.commit()
    return row


def multi_project(client, user, client_id, services) -> dict:
    res = client.post(
        "/api/projects",
        json={"title": "Diwali campaign", "client_id": client_id, "service_ids": [s.id for s in services]},
        headers=auth_header(user),
    )
    assert res.status_code == 201, res.text
    return res.json()


def raise_invoice(client, user, body):
    return client.post("/api/invoices", json=body, headers=auth_header(user))


# ---------- projects ----------


def test_project_bills_several_services(client, users, client_row, two_services):
    p = multi_project(client, users["manager"], client_row.id, two_services)
    assert sorted(s["title"] for s in p["services"]) == ["Creative design", "Media buying"]
    assert {s["sac_code"] for s in p["services"]} == {"998366", "998391"}
    assert p["code"].startswith("PRJ-")


def test_project_needs_a_service(client, users, client_row):
    res = client.post(
        "/api/projects", json={"title": "No service", "client_id": client_row.id, "service_ids": []},
        headers=auth_header(users["manager"]),
    )
    assert res.status_code == 422


def test_project_services_can_be_replaced(client, users, client_row, two_services):
    p = multi_project(client, users["manager"], client_row.id, two_services)
    res = client.patch(
        f"/api/projects/{p['id']}", json={"service_ids": [two_services[1].id]}, headers=auth_header(users["manager"])
    )
    assert res.status_code == 200, res.text
    assert [s["title"] for s in res.json()["services"]] == ["Creative design"]


# ---------- invoice lines ----------


def test_invoice_has_a_line_per_service_with_its_own_sac(client, users, client_row, two_services):
    media, design = two_services
    p = multi_project(client, users["manager"], client_row.id, two_services)
    res = raise_invoice(client, users["exec"], {"project_id": p["id"], "lines": [
        {"service_id": media.id, "taxable_value": 100000},
        {"service_id": design.id, "taxable_value": 50000, "description": "Festive creatives"},
    ]})
    assert res.status_code == 201, res.text
    inv = res.json()
    assert [(l["service_title"], l["sac_code"], l["taxable_value"]) for l in inv["lines"]] == [
        ("Media buying", "998366", 100000), ("Creative design", "998391", 50000),
    ]
    assert inv["lines"][1]["description"] == "Festive creatives"
    assert inv["taxable_value"] == 150000
    assert inv["gst_amount"] == 27000
    assert inv["cgst"] == inv["sgst"] == 13500  # Acme's GSTIN is in Maharashtra
    assert inv["total_amount"] == 177000
    assert inv["expected_tds"] == 3000  # 2% default


def test_a_line_must_be_one_of_the_projects_services(client, users, client_row, two_services):
    p = multi_project(client, users["manager"], client_row.id, two_services[:1])
    res = raise_invoice(client, users["exec"], {"project_id": p["id"], "lines": [
        {"service_id": two_services[1].id, "taxable_value": 1000},
    ]})
    assert res.status_code == 400


def test_a_service_appears_once_per_invoice(client, users, client_row, two_services):
    p = multi_project(client, users["manager"], client_row.id, two_services)
    line = {"service_id": two_services[0].id, "taxable_value": 1000}
    res = raise_invoice(client, users["exec"], {"project_id": p["id"], "lines": [line, line]})
    assert res.status_code == 400


def test_an_invoice_needs_an_amount(client, users, client_row, two_services):
    p = multi_project(client, users["manager"], client_row.id, two_services)
    assert raise_invoice(client, users["exec"], {"project_id": p["id"]}).status_code == 422
    assert raise_invoice(client, users["exec"], {"project_id": p["id"], "lines": []}).status_code == 422


def test_editing_a_proforma_replaces_its_lines(client, users, client_row, two_services):
    media, design = two_services
    p = multi_project(client, users["manager"], client_row.id, two_services)
    inv = raise_invoice(client, users["exec"], {"project_id": p["id"], "lines": [
        {"service_id": media.id, "taxable_value": 1000},
    ]}).json()
    res = client.patch(f"/api/invoices/{inv['id']}", json={"lines": [
        {"service_id": design.id, "taxable_value": 4000},
        {"service_id": media.id, "taxable_value": 2000},
    ]}, headers=auth_header(users["exec"]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert [l["service_title"] for l in body["lines"]] == ["Creative design", "Media buying"]
    assert body["taxable_value"] == 6000
    assert body["sac_code"] == "998391"  # the header mirrors the first line


def test_an_old_invoice_reads_as_one_line(client, users, client_row, db):
    row = ClientInvoice(
        client_id=client_row.id, agent_id=house_agent(db).id, service_title="Retainer", sac_code="998311",
        taxable_value=1000, gst_rate=18, gst_amount=180, cgst=90, sgst=90, total_amount=1180,
    )
    db.add(row)
    db.commit()
    body = client.get(f"/api/invoices/{row.id}", headers=auth_header(users["exec"])).json()
    assert [(l["service_title"], l["taxable_value"], l["gst_amount"]) for l in body["lines"]] == [("Retainer", 1000, 180)]


# ---------- IGST by GSTIN state ----------


@pytest.mark.parametrize(
    ("gstin", "asked", "interstate"),
    [
        ("29CCCCC2222C1Z5", False, True),  # Karnataka client: IGST whatever is asked
        ("27BBBBB1111B1Z5", True, False),  # Maharashtra client: CGST + SGST whatever is asked
        (None, True, True),  # no GSTIN: the choice stands
        ("jhgfhfhfh", False, False),  # a GSTIN saved before checks: no usable state, so the choice stands
        (None, False, False),
    ],
)
def test_igst_follows_the_clients_gstin_state(client, users, db, gstin, asked, interstate):
    row = new_client(db, gst_number=gstin)
    project = Project(code=f"IGST-{row.id}", title="Work", client_id=row.id, service_id=default_service_id())
    project.services = [db.get(Service, default_service_id())]
    db.add(project)
    db.commit()
    inv = raise_invoice(client, users["exec"], {"project_id": project.id, "taxable_value": 1000, "is_interstate": asked})
    assert inv.status_code == 201, inv.text
    body = inv.json()
    assert body["is_interstate"] is interstate
    assert (body["igst"] > 0) is interstate


# ---------- invoice date ----------


def test_invoice_date_can_be_backdated_within_the_month(client, users, client_row, two_services):
    p = multi_project(client, users["manager"], client_row.id, two_services)
    first = business_date().replace(day=1)
    body = {"project_id": p["id"], "taxable_value": 1000}
    ok = raise_invoice(client, users["exec"], {**body, "proforma_date": first.isoformat()})
    assert ok.status_code == 201, ok.text
    assert ok.json()["proforma_date"] == first.isoformat()
    last_month = first - timedelta(days=1)
    assert raise_invoice(client, users["exec"], {**body, "proforma_date": last_month.isoformat()}).status_code == 400
    tomorrow = business_date() + timedelta(days=1)
    assert raise_invoice(client, users["exec"], {**body, "proforma_date": tomorrow.isoformat()}).status_code == 400


# ---------- coordinator approval sends the proforma ----------


def test_only_a_coordinator_cfo_or_ceo_sends_a_proforma_to_the_client(client, users, client_row, two_services):
    p = multi_project(client, users["manager"], client_row.id, two_services)
    inv = raise_invoice(client, users["exec"], {"project_id": p["id"], "taxable_value": 1000}).json()
    url = f"/api/invoices/{inv['id']}/send-to-client"
    assert client.post(url, headers=auth_header(users["exec"])).status_code == 403
    offline = client.post(
        f"/api/invoices/{inv['id']}/client-approval", json={"note": "Approved on call"}, headers=auth_header(users["exec"])
    )
    assert offline.status_code == 403
    assert client.post(url, headers=auth_header(users["manager"])).status_code == 403
    assert client.post(url, headers=auth_header(users["coordinator"])).status_code == 200


# ---------- automatic TDS on receipts ----------


def receive(client, user, invoice_id, amount):
    body = {"amount": amount, "payment_date": business_date().isoformat(), "bank_name": "HDFC Bank"}
    return client.post(f"/api/invoices/{invoice_id}/payments", json=body, headers=auth_header(user))


def test_receipt_net_of_tds_settles_the_invoice(client, users, client_row):
    inv = issued_invoice(client, users, client_row, tds_rate=2)  # 1,180 total, TDS 20
    res = receive(client, users["exec"], inv["id"], 1160)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "Fully Paid"
    assert body["payments"][0]["tds_deducted"] == 20


def test_part_receipt_counts_no_tds_until_the_rest_is_cleared(client, users, client_row):
    inv = issued_invoice(client, users, client_row, tds_rate=2)
    first = receive(client, users["exec"], inv["id"], 500).json()
    assert first["status"] == "Partially Paid"
    assert first["payments"][0]["tds_deducted"] == 0
    second = receive(client, users["exec"], inv["id"], 660).json()
    assert second["status"] == "Fully Paid"
    assert second["payments"][1]["tds_deducted"] == 20


def test_receipt_of_the_full_amount_counts_no_tds(client, users, client_row):
    inv = issued_invoice(client, users, client_row, tds_rate=2)
    body = receive(client, users["exec"], inv["id"], 1180).json()
    assert body["status"] == "Fully Paid"
    assert body["payments"][0]["tds_deducted"] == 0


# ---------- clients ----------


def test_msme_number_is_kept_only_for_an_msme(client, users):
    base = {"business_name": "Small Co", "email": "a@small.example"}
    made = client.post(
        "/api/clients", json={**base, "msme": True, "msme_number": "UDYAM-MH-26-0012345"},
        headers=auth_header(users["manager"]),
    ).json()
    assert (made["msme"], made["msme_number"]) == (True, "UDYAM-MH-26-0012345")
    off = client.patch(f"/api/clients/{made['id']}", json={"msme": False}, headers=auth_header(users["manager"])).json()
    assert (off["msme"], off["msme_number"]) == (False, None)
    bad = client.post(
        "/api/clients", json={**base, "msme": True, "msme_number": "12345"}, headers=auth_header(users["manager"])
    )
    assert bad.status_code == 422
