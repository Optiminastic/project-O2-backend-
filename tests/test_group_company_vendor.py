"""Group company vendors are seen and managed by the CFO and CEO only; vendors carry a default TDS section."""

from app.models import PaymentApproval, Vendor, VendorInvoice
from app.services.numbering import business_date
from tests.conftest import auth_header, project_id_for


def vendor(client, user, **overrides):
    body = {"business_name": "Optiminastic Labs", "email": "accounts@labs.example", **overrides}
    return client.post("/api/vendors", json=body, headers=auth_header(user))


def group_vendor(client, users):
    res = vendor(client, users["cfo"], is_group_company=True)
    assert res.status_code == 201, res.text
    return res.json()


def test_only_cfo_or_ceo_create_a_group_company(client, users):
    assert vendor(client, users["manager"], is_group_company=True).status_code == 403
    assert vendor(client, users["exec"], is_group_company=True).status_code == 403
    assert vendor(client, users["ceo"], is_group_company=True).status_code == 201
    assert group_vendor(client, users)["is_group_company"] is True


def test_group_company_is_hidden_from_other_roles(client, users):
    gv = group_vendor(client, users)
    vendor(client, users["manager"], business_name="Print House", email="a@print.example")
    for role in ("manager", "exec"):
        names = [v["business_name"] for v in client.get("/api/vendors", headers=auth_header(users[role])).json()]
        assert names == ["Print House"]
        assert client.get(f"/api/vendors/{gv['id']}", headers=auth_header(users[role])).status_code == 404
    names = {v["business_name"] for v in client.get("/api/vendors", headers=auth_header(users["cfo"])).json()}
    assert names == {"Print House", "Optiminastic Labs"}


def test_only_cfo_or_ceo_edit_or_mark_a_group_company(client, users):
    gv = group_vendor(client, users)
    other = vendor(client, users["manager"], business_name="Print House", email="a@print.example").json()
    assert client.patch(f"/api/vendors/{gv['id']}", json={"phone": "+91 98100 11111"}, headers=auth_header(users["manager"])).status_code == 404
    assert client.patch(f"/api/vendors/{other['id']}", json={"is_group_company": True}, headers=auth_header(users["manager"])).status_code == 403
    res = client.patch(f"/api/vendors/{other['id']}", json={"is_group_company": True}, headers=auth_header(users["ceo"]))
    assert res.status_code == 200 and res.json()["is_group_company"] is True


def test_group_company_bills_and_payments_are_hidden(client, db, users, client_row):
    gv = group_vendor(client, users)
    bill = VendorInvoice(vendor_id=gv["id"], invoice_number="GC-1", invoice_date=business_date(), invoice_amount=1000)
    db.add(bill)
    db.flush()
    db.add(PaymentApproval(payee_name="Optiminastic Labs", amount=1000, vendor_invoice_id=bill.id))
    db.add(PaymentApproval(payee_name="Office rent", amount=500))
    db.commit()
    manager, cfo = auth_header(users["manager"]), auth_header(users["cfo"])
    assert client.get("/api/vendors/invoices/all", headers=manager).json() == []
    assert len(client.get("/api/vendors/invoices/all", headers=cfo).json()) == 1
    assert [a["payee_name"] for a in client.get("/api/approvals", headers=manager).json()] == ["Office rent"]
    assert len(client.get("/api/approvals", headers=cfo).json()) == 2


def test_on_a_project_a_group_company_shows_its_cost_but_not_its_name(client, db, users, client_row):
    gv = group_vendor(client, users)
    pid = project_id_for(client_row)
    res = client.patch(
        f"/api/projects/{pid}", json={"vendors": [{"vendor_id": gv["id"], "work_percent": 50, "agreed_cost": 4000}]},
        headers=auth_header(users["cfo"]),
    )
    assert res.status_code == 200, res.text
    row = client.get(f"/api/projects/{pid}", headers=auth_header(users["manager"])).json()["vendors"][0]
    assert (row["vendor_name"], row["vendor_email"], row["agreed_cost"]) == ("Group company (restricted)", "", 4000)
    listed = client.get("/api/projects", headers=auth_header(users["manager"])).json()
    assert {v["vendor_name"] for p in listed for v in p["vendors"]} == {"Group company (restricted)"}
    assert client.get(f"/api/projects/{pid}", headers=auth_header(users["cfo"])).json()["vendors"][0]["vendor_name"] == "Optiminastic Labs"


def test_a_manager_keeps_but_cannot_change_a_group_company_row(client, users, client_row):
    gv = group_vendor(client, users)
    other = vendor(client, users["manager"], business_name="Print House", email="a@print.example").json()
    pid = project_id_for(client_row)
    group_row = {"vendor_id": gv["id"], "work_percent": 50, "agreed_cost": 4000}
    client.patch(f"/api/projects/{pid}", json={"vendors": [group_row]}, headers=auth_header(users["cfo"]))
    url, manager = f"/api/projects/{pid}", auth_header(users["manager"])
    keeps = client.patch(url, json={"vendors": [group_row, {"vendor_id": other["id"], "work_percent": 50, "agreed_cost": 1000}]}, headers=manager)
    assert keeps.status_code == 200, keeps.text
    assert client.patch(url, json={"vendors": [{**group_row, "agreed_cost": 9000}]}, headers=manager).status_code == 403
    assert client.patch(url, json={"vendors": []}, headers=manager).status_code == 403
    assert client.get(f"{url}/vendors/{gv['id']}/work-order.pdf", headers=manager).status_code == 403


def test_vendor_keeps_its_default_tds_section(client, users):
    res = vendor(client, users["manager"], default_tds_section="194J_PROFESSIONAL")
    assert res.status_code == 201, res.text
    assert res.json()["default_tds_section"] == "194J_PROFESSIONAL"
    assert vendor(client, users["manager"], business_name="X", default_tds_section="BOGUS").status_code == 422
