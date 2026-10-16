"""The Coordinator approves proformas (which sends them to the client) and otherwise only reads."""

from tests.conftest import auth_header, project_id_for


def proforma(client, user, client_row):
    body = {"project_id": project_id_for(client_row), "taxable_value": 50000}
    res = client.post("/api/invoices", json=body, headers=auth_header(user))
    assert res.status_code == 201, res.text
    return res.json()


def test_coordinator_approves_a_proforma_and_it_goes_to_the_client(client, users, client_row):
    inv = proforma(client, users["exec"], client_row)
    res = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["coordinator"]))
    assert res.status_code == 200, res.text
    assert res.json()["invoice"]["status"] == "Awaiting Client"


def test_finance_manager_no_longer_approves_proformas(client, users, client_row):
    inv = proforma(client, users["exec"], client_row)
    res = client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users["manager"]))
    assert res.status_code == 403
    assert "Coordinator" in res.json()["detail"]
    offline = client.post(
        f"/api/invoices/{inv['id']}/client-approval", json={"note": "Approved on call"}, headers=auth_header(users["manager"])
    )
    assert offline.status_code == 403


def test_cfo_and_ceo_can_still_approve_proformas(client, users, client_row):
    for role in ("cfo", "ceo"):
        inv = proforma(client, users["exec"], client_row)
        assert client.post(f"/api/invoices/{inv['id']}/send-to-client", headers=auth_header(users[role])).status_code == 200


def test_coordinator_reads_but_cannot_create_or_issue(client, users, client_row):
    headers = auth_header(users["coordinator"])
    inv = proforma(client, users["exec"], client_row)
    assert client.get("/api/invoices", headers=headers).status_code == 200
    assert client.get(f"/api/invoices/{inv['id']}", headers=headers).status_code == 200
    assert client.get("/api/projects", headers=headers).status_code == 200
    assert client.get("/api/clients", headers=headers).status_code == 200
    body = {"project_id": project_id_for(client_row), "taxable_value": 1000}
    assert client.post("/api/invoices", json=body, headers=headers).status_code == 403
    assert client.post(f"/api/invoices/{inv['id']}/issue", headers=headers).status_code == 403


def test_coordinator_has_no_access_to_approvals_or_admin(client, users):
    headers = auth_header(users["coordinator"])
    assert client.get("/api/approvals", headers=headers).status_code == 403
    assert client.get("/api/audit", headers=headers).status_code == 403
    assert client.get("/api/team/members", headers=headers).status_code == 403
    assert client.get("/api/taxation/summary", headers=headers).status_code == 403


def test_ceo_can_invite_a_coordinator(client, users):
    body = {"email": "coord2@optiminastic.com", "name": "Coord Two", "role": "COORDINATOR"}
    res = client.post("/api/team/invitations", json=body, headers=auth_header(users["ceo"]))
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "COORDINATOR"
