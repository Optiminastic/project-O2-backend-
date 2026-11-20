from datetime import timedelta

from app.models import Agent, ClientInvoice, InvoiceStatus, Vendor
from app.routers.cash import split_equally
from app.services.numbering import business_date
from tests.conftest import assignment_for, auth_header, default_service_id


def _codes(db, client_row, count=2):
    ids = []
    for n in range(count):
        v = Vendor(business_name=f"Cash Vendor {n}", email=f"v{n}@cash.example")
        db.add(v)
        db.commit()
        ids.append(assignment_for(v.id, client_row.id))
    return ids


def _agent(db, active=True):
    a = Agent(business_name="Rakesh Agencies", commission_rate=2, is_active=active)
    db.add(a)
    db.commit()
    return a


def test_split_is_equal_to_the_paisa():
    assert split_equally(100, 3) == [33.34, 33.33, 33.33]
    assert round(sum(split_equally(1000.01, 7)), 2) == 1000.01


def test_cash_received_is_divided_over_the_ups_codes(client, users, db, client_row):
    codes = _codes(db, client_row)
    body = {"direction": "IN", "entry_date": str(business_date()), "amount": 1001, "assignment_ids": codes}
    res = client.post("/api/cash", json=body, headers=auth_header(users["exec"]))
    assert res.status_code == 201, res.text
    entry = res.json()
    assert [l["amount"] for l in entry["lines"]] == [500.5, 500.5]
    assert all(l["code"] for l in entry["lines"])
    listed = client.get("/api/cash?direction=IN", headers=auth_header(users["exec"])).json()
    assert [e["id"] for e in listed] == [entry["id"]]


def test_cash_outward_needs_an_active_agent(client, users, db, client_row):
    codes = _codes(db, client_row, 1)
    h = auth_header(users["manager"])
    body = {"direction": "OUT", "entry_date": str(business_date()), "amount": 500, "assignment_ids": codes}
    assert client.post("/api/cash", json=body, headers=h).status_code == 400
    inactive = _agent(db, active=False)
    assert client.post("/api/cash", json={**body, "agent_id": inactive.id}, headers=h).status_code == 400
    agent = _agent(db)
    res = client.post("/api/cash", json={**body, "agent_id": agent.id}, headers=h)
    assert res.status_code == 201, res.text
    assert res.json()["agent_name"] == "Rakesh Agencies"
    # Cash received is not paid to anyone.
    assert client.post("/api/cash", json={**body, "direction": "IN", "agent_id": agent.id}, headers=h).status_code == 400


def test_bad_entries_are_refused(client, users, db, client_row):
    codes = _codes(db, client_row, 1)
    h = auth_header(users["exec"])
    base = {"direction": "IN", "entry_date": str(business_date()), "amount": 10, "assignment_ids": codes}
    assert client.post("/api/cash", json={**base, "assignment_ids": []}, headers=h).status_code == 422
    assert client.post("/api/cash", json={**base, "assignment_ids": codes * 2}, headers=h).status_code == 422
    assert client.post("/api/cash", json={**base, "assignment_ids": [999999]}, headers=h).status_code == 404
    assert client.post("/api/cash", json={**base, "amount": 0}, headers=h).status_code == 422
    future = str(business_date() + timedelta(days=2))
    assert client.post("/api/cash", json={**base, "entry_date": future}, headers=h).status_code == 400
    # Coordinators cannot record cash.
    assert client.post("/api/cash", json=base, headers=auth_header(users["coordinator"])).status_code == 403


def test_only_managers_delete_an_entry(client, users, db, client_row):
    codes = _codes(db, client_row, 1)
    body = {"direction": "IN", "entry_date": str(business_date()), "amount": 10, "assignment_ids": codes}
    entry = client.post("/api/cash", json=body, headers=auth_header(users["exec"])).json()
    assert client.delete(f"/api/cash/{entry['id']}", headers=auth_header(users["exec"])).status_code == 403
    assert client.delete(f"/api/cash/{entry['id']}", headers=auth_header(users["manager"])).status_code == 204
    assert client.get("/api/cash", headers=auth_header(users["exec"])).json() == []


def test_overview_is_month_by_month(client, users, db, client_row):
    codes = _codes(db, client_row, 1)
    today = business_date()
    for number, status, taxable in (("INV/C1", InvoiceStatus.SENT, 1000), ("INV/C2", InvoiceStatus.CANCELLED, 5000)):
        db.add(ClientInvoice(client_id=client_row.id, agent_id=client_row.agent_id, service_id=default_service_id(),
                             status=status, invoice_number=number, invoice_date=today, taxable_value=taxable,
                             gst_amount=taxable * 0.18, total_amount=taxable * 1.18))
    db.commit()
    h = auth_header(users["manager"])
    agent = _agent(db)
    client.post("/api/cash", json={"direction": "IN", "entry_date": str(today), "amount": 700, "assignment_ids": codes}, headers=h)
    client.post("/api/cash", json={"direction": "OUT", "entry_date": str(today), "amount": 200, "assignment_ids": codes,
                                   "agent_id": agent.id}, headers=h)
    rows = client.get("/api/cash/overview?months=3", headers=h).json()
    assert len(rows) == 3 and rows[-1]["month"] == today.strftime("%Y-%m")
    now = rows[-1]
    assert (now["invoice_taxable"], now["invoice_gst"], now["invoice_total"], now["invoice_count"]) == (1000, 180, 1180, 1)
    assert (now["cash_received"], now["cash_paid"]) == (700, 200)
    assert rows[0]["cash_received"] == 0


def test_ups_codes_are_listed(client, users, db, client_row):
    codes = _codes(db, client_row, 1)
    rows = client.get("/api/cash/codes", headers=auth_header(users["exec"])).json()
    assert codes[0] in [r["id"] for r in rows]
    assert all(r["code"] and r["project_code"] for r in rows)


def test_executives_see_the_agents_cash_can_go_to(client, users, db):
    agent = _agent(db)
    _agent(db, active=False)
    rows = client.get("/api/cash/agents", headers=auth_header(users["exec"])).json()
    assert agent.id in [r["id"] for r in rows] and len([r for r in rows if r["business_name"] == "Rakesh Agencies"]) == 1
