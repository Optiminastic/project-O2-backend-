"""Every client and invoice has an agent; "Opti" is the in-house default with no commission."""

from app.models import Agent
from app.services.agents import HOUSE_AGENT_NAME, ensure_house_agent, house_agent
from tests.conftest import auth_header, default_service_id


def make_agent(db, name="Techcores", rate=5.0) -> Agent:
    agent = Agent(business_name=name, email=f"{name.lower()}@example.com", commission_rate=rate)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def new_client(client, user, **overrides):
    body = {"business_name": "Globex", "email": "ap@globex.example", **overrides}
    res = client.post("/api/clients", json=body, headers=auth_header(user))
    assert res.status_code == 201, res.text
    return res.json()


def new_invoice(client, user, client_id, **overrides):
    body = {"client_id": client_id, "taxable_value": 1000, "service_id": default_service_id(), **overrides}
    res = client.post("/api/invoices", json=body, headers=auth_header(user))
    assert res.status_code == 201, res.text
    return res.json()


def issue(client, users, invoice_id):
    sent = client.post(f"/api/invoices/{invoice_id}/send-to-client", headers=auth_header(users["exec"])).json()
    token = sent["link_url"].rsplit("/", 1)[-1]
    client.post(f"/api/public/invoices/{token}/approve", json={"name": "A. Client"})
    res = client.post(f"/api/invoices/{invoice_id}/issue", headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text


# ---------- The house agent ----------


def test_house_agent_exists_with_no_commission(db):
    opti = house_agent(db)
    assert opti.business_name == HOUSE_AGENT_NAME == "Opti"
    assert opti.is_house is True
    assert opti.commission_rate == 0
    assert opti.is_active is True


def test_ensure_house_agent_is_idempotent(db):
    first = house_agent(db).id
    ensure_house_agent(db)
    ensure_house_agent(db)
    assert db.query(Agent).filter(Agent.is_house.is_(True)).count() == 1
    assert house_agent(db).id == first


def test_house_agent_commission_and_status_are_fixed(client, users, db):
    opti_id = house_agent(db).id
    headers = auth_header(users["ceo"])
    assert client.patch(f"/api/agents/{opti_id}", json={"commission_rate": 5}, headers=headers).status_code == 400
    assert client.patch(f"/api/agents/{opti_id}", json={"is_active": False}, headers=headers).status_code == 400
    assert client.patch(f"/api/agents/{opti_id}", json={"phone": "+91 98200 00000"}, headers=headers).status_code == 200
    assert client.delete(f"/api/agents/{opti_id}", headers=headers).status_code == 400


# ---------- Clients ----------


def test_client_without_agent_gets_opti(client, users, db):
    created = new_client(client, users["exec"])
    assert created["agent_id"] == house_agent(db).id


def test_client_keeps_the_chosen_agent(client, users, db):
    agent = make_agent(db)
    assert new_client(client, users["exec"], agent_id=agent.id)["agent_id"] == agent.id


def test_client_with_unknown_agent_is_rejected(client, users):
    res = client.post(
        "/api/clients", json={"business_name": "X", "email": "x@x.example", "agent_id": 9999}, headers=auth_header(users["exec"])
    )
    assert res.status_code == 404


def test_clearing_a_client_agent_falls_back_to_opti(client, users, db):
    agent = make_agent(db)
    created = new_client(client, users["exec"], agent_id=agent.id)
    res = client.patch(f"/api/clients/{created['id']}", json={"agent_id": None}, headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    assert res.json()["agent_id"] == house_agent(db).id


# ---------- Invoices ----------


def test_invoice_defaults_to_the_clients_agent(client, users, db):
    agent = make_agent(db)
    c = new_client(client, users["exec"], agent_id=agent.id)
    assert new_invoice(client, users["exec"], c["id"])["agent_id"] == agent.id


def test_invoice_for_a_direct_client_is_credited_to_opti(client, users, db):
    c = new_client(client, users["exec"])
    assert new_invoice(client, users["exec"], c["id"])["agent_id"] == house_agent(db).id


def test_proforma_agent_can_change_but_issued_invoice_agent_is_locked(client, users, db):
    first, second = make_agent(db, "First"), make_agent(db, "Second", 3)
    c = new_client(client, users["exec"], agent_id=first.id)
    inv = new_invoice(client, users["exec"], c["id"])

    res = client.patch(f"/api/invoices/{inv['id']}", json={"agent_id": second.id}, headers=auth_header(users["exec"]))
    assert res.status_code == 200, res.text
    assert res.json()["agent_id"] == second.id

    issue(client, users, inv["id"])
    res = client.patch(f"/api/invoices/{inv['id']}", json={"agent_id": first.id}, headers=auth_header(users["manager"]))
    assert res.status_code == 423


# ---------- Deleting an agent ----------


def test_deleting_an_agent_moves_their_clients_and_proformas_to_opti(client, users, db):
    agent = make_agent(db)
    c = new_client(client, users["exec"], agent_id=agent.id)
    inv = new_invoice(client, users["exec"], c["id"])

    assert client.delete(f"/api/agents/{agent.id}", headers=auth_header(users["ceo"])).status_code == 204
    opti_id = house_agent(db).id
    assert client.get(f"/api/clients/{c['id']}", headers=auth_header(users["exec"])).json()["agent_id"] == opti_id
    assert client.get(f"/api/invoices/{inv['id']}", headers=auth_header(users["exec"])).json()["agent_id"] == opti_id


def test_agent_with_issued_invoices_cannot_be_deleted(client, users, db):
    agent = make_agent(db)
    c = new_client(client, users["exec"], agent_id=agent.id)
    issue(client, users, new_invoice(client, users["exec"], c["id"])["id"])
    res = client.delete(f"/api/agents/{agent.id}", headers=auth_header(users["ceo"]))
    assert res.status_code == 409
    assert "deactivate" in res.json()["detail"].lower()


# ---------- Agent picker for every finance role ----------


def test_executives_get_a_names_only_agent_list_with_opti_first(client, users, db):
    make_agent(db, "Alpha")
    inactive = make_agent(db, "Dormant")
    inactive.is_active = False
    db.commit()

    res = client.get("/api/agents/options", headers=auth_header(users["exec"]))
    assert res.status_code == 200, res.text
    options = res.json()
    assert [o["business_name"] for o in options] == ["Opti", "Alpha"]
    assert set(options[0]) == {"id", "business_name", "is_house"}
