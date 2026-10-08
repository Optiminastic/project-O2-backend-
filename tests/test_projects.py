"""Projects: one client project, several vendors with a share of the work, a budget, and work orders."""

from datetime import date

import pytest
from sqlalchemy import text

from app.database import engine
from app.models import AuditLog, Vendor, VendorAllocation, VendorInvoice
from app.services.numbering import business_date, financial_year, month_end
from tests.conftest import auth_header


@pytest.fixture
def vendors(db):
    rows = [
        Vendor(business_name="Quanta Research", email="ops@quanta.example"),
        Vendor(business_name="Field Force", email="hello@fieldforce.example"),
    ]
    db.add_all(rows)
    db.commit()
    for v in rows:
        db.refresh(v)
    return rows


def project_body(client_row, vendors, **overrides):
    body = {
        "code": "axis-q4",
        "title": "Axis Q4 market study",
        "brand": "Axis Bank",
        "description": "Survey 2,000 customers across 8 cities.",
        "client_id": client_row.id,
        "budget": 500000,
        "start_date": "2026-10-01",
        "end_date": "2026-11-20",
        "vendors": [
            {"vendor_id": vendors[0].id, "work_percent": 60, "agreed_cost": 240000},
            {"vendor_id": vendors[1].id, "work_percent": 40, "agreed_cost": 160000},
        ],
        **overrides,
    }
    return body


def create(client, user, body):
    return client.post("/api/projects", json=body, headers=auth_header(user))


def test_project_holds_its_client_vendors_and_cost(client, users, client_row, vendors):
    res = create(client, users["manager"], project_body(client_row, vendors))
    assert res.status_code == 201, res.text
    p = res.json()
    assert p["code"] == "AXIS-Q4"
    assert p["client_name"] == "Acme Traders"
    assert [v["vendor_name"] for v in p["vendors"]] == ["Quanta Research", "Field Force"]
    assert p["committed_cost"] == 400000
    assert p["remaining_budget"] == 100000
    assert p["over_budget"] is False
    # Report is due on the last day of the end date's month unless chosen.
    assert p["expected_report_date"] == "2026-11-30"
    assert db_audit(client, "Created project")


def db_audit(client, action):
    from app.database import SessionLocal

    with SessionLocal() as s:
        return s.query(AuditLog).filter(AuditLog.action == action).count() == 1


def test_report_date_can_be_chosen(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors, expected_report_date="2026-12-05")).json()
    assert p["expected_report_date"] == "2026-12-05"


def test_report_date_without_an_end_date_is_this_month_end(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors, start_date=None, end_date=None)).json()
    assert p["expected_report_date"] == month_end(business_date()).isoformat()


def test_work_shares_cannot_exceed_100_percent(client, users, client_row, vendors):
    body = project_body(client_row, vendors)
    body["vendors"][1]["work_percent"] = 50
    res = create(client, users["manager"], body)
    assert res.status_code == 400
    assert "110%" in res.json()["detail"]


def test_a_vendor_is_listed_once(client, users, client_row, vendors):
    body = project_body(client_row, vendors)
    body["vendors"][1]["vendor_id"] = vendors[0].id
    assert create(client, users["manager"], body).status_code == 400


def test_project_codes_are_unique_ignoring_case(client, users, client_row, vendors):
    create(client, users["manager"], project_body(client_row, vendors))
    res = create(client, users["manager"], project_body(client_row, vendors, code="AXIS-Q4"))
    assert res.status_code == 409


def test_end_date_cannot_be_before_start(client, users, client_row, vendors):
    res = create(client, users["manager"], project_body(client_row, vendors, end_date="2026-09-01"))
    assert res.status_code == 400


def test_over_budget_is_flagged(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors, budget=350000)).json()
    assert p["over_budget"] is True
    assert p["remaining_budget"] == -50000


def test_finance_executive_can_view_but_not_create(client, users, client_row, vendors):
    assert create(client, users["exec"], project_body(client_row, vendors)).status_code == 403
    create(client, users["manager"], project_body(client_row, vendors))
    assert len(client.get("/api/projects", headers=auth_header(users["exec"])).json()) == 1


def test_month_filter_shows_projects_running_that_month(client, users, client_row, vendors):
    create(client, users["manager"], project_body(client_row, vendors))  # 1 Oct - 20 Nov
    h = auth_header(users["exec"])
    assert len(client.get("/api/projects?month=2026-11", headers=h).json()) == 1
    assert len(client.get("/api/projects?month=2026-12", headers=h).json()) == 0
    assert len(client.get("/api/projects?month=2026-09", headers=h).json()) == 0


def test_editing_vendors_keeps_work_orders_and_flags_changed_terms(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    h = auth_header(users["manager"])
    client.get(f"/api/projects/{p['id']}/vendors/{vendors[0].id}/work-order.pdf", headers=h)

    res = client.patch(f"/api/projects/{p['id']}", json={"vendors": [
        {"vendor_id": vendors[0].id, "work_percent": 70, "agreed_cost": 280000},
    ]}, headers=h)
    assert res.status_code == 200, res.text
    (row,) = res.json()["vendors"]
    assert row["work_order_number"] == f"WO/{financial_year(business_date())}/0001"
    assert row["work_order_outdated"] is True
    assert res.json()["committed_cost"] == 280000


def test_work_order_pdf_gets_one_number(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    h = auth_header(users["manager"])
    url = f"/api/projects/{p['id']}/vendors/{vendors[1].id}/work-order.pdf"
    first = client.get(url, headers=h)
    assert first.status_code == 200
    assert first.content.startswith(b"%PDF")
    assert "WO-" in first.headers["content-disposition"]
    client.get(url, headers=h)
    detail = client.get(f"/api/projects/{p['id']}", headers=h).json()
    numbers = [v["work_order_number"] for v in detail["vendors"]]
    assert numbers == [None, f"WO/{financial_year(business_date())}/0001"]


def test_sending_a_work_order_without_email_set_up(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    res = client.post(f"/api/projects/{p['id']}/vendors/{vendors[0].id}/work-order/send", headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["emailed"] is False
    assert body["to_email"] == "ops@quanta.example"
    assert body["work_order_number"].startswith("WO/")


def record_vendor_invoice(client, user, vendor_id, project_id, amount=100000):
    return client.post("/api/vendors/invoices", json={
        "vendor_id": vendor_id, "project_id": project_id, "invoice_number": f"V-{amount}",
        "invoice_date": "2026-10-15", "invoice_amount": amount, "gst_amount": amount * 0.18,
    }, headers=auth_header(user))


def test_vendor_invoices_count_against_the_project(client, users, client_row, vendors, db):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    assert record_vendor_invoice(client, users["exec"], vendors[0].id, p["id"], 250000).status_code == 201
    detail = client.get(f"/api/projects/{p['id']}", headers=auth_header(users["exec"])).json()
    assert detail["invoiced_cost"] == 250000
    first = detail["vendors"][0]
    assert first["invoiced"] == 250000
    assert first["over_invoiced"] is True  # agreed 2,40,000
    assert [i["invoice_number"] for i in detail["vendor_invoices"]] == ["V-250000"]


def test_vendor_invoice_must_be_for_a_vendor_on_the_project(client, users, client_row, vendors, db):
    body = project_body(client_row, vendors)
    body["vendors"] = body["vendors"][:1]
    p = create(client, users["manager"], body).json()
    assert record_vendor_invoice(client, users["exec"], vendors[1].id, p["id"]).status_code == 400


def test_vendor_with_invoices_cannot_be_removed_and_project_cannot_be_deleted(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    record_vendor_invoice(client, users["exec"], vendors[1].id, p["id"])
    h = auth_header(users["manager"])
    res = client.patch(f"/api/projects/{p['id']}", json={"vendors": [
        {"vendor_id": vendors[0].id, "work_percent": 60, "agreed_cost": 240000},
    ]}, headers=h)
    assert res.status_code == 400
    assert "Field Force" in res.json()["detail"]
    assert client.delete(f"/api/projects/{p['id']}", headers=h).status_code == 409


def test_project_without_invoices_can_be_deleted(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    h = auth_header(users["manager"])
    assert client.delete(f"/api/projects/{p['id']}", headers=h).status_code == 204
    assert client.get(f"/api/projects/{p['id']}", headers=h).status_code == 404


def test_old_allocations_become_projects(db, client_row, vendors):
    from app.schema_upgrades import PROJECTS, _run

    alloc = VendorAllocation(vendor_id=vendors[0].id, client_id=client_row.id, project_name="Legacy study",
                             agreed_cost=90000, allocation_percent=100, end_date=date(2026, 10, 20))
    db.add(alloc)
    db.flush()
    db.add(VendorInvoice(vendor_id=vendors[0].id, allocation_id=alloc.id, invoice_number="OLD-1",
                         invoice_date=date(2026, 10, 1), invoice_amount=90000))
    db.commit()

    _run(engine, PROJECTS)
    _run(engine, PROJECTS)  # idempotent

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT code, title, budget FROM projects")).all()
        assert rows == [(f"ALLOC-{alloc.id}", "Legacy study", 90000)]
        assert conn.execute(text("SELECT count(*) FROM project_vendors")).scalar_one() == 1
        assert conn.execute(text("SELECT project_id IS NOT NULL FROM vendor_invoices")).scalar_one() is True


def test_erase_covers_projects():
    from app.services.wipe import DELETE_ORDER

    assert DELETE_ORDER.index("vendor_invoices") < DELETE_ORDER.index("project_vendors") < DELETE_ORDER.index("projects")
    assert DELETE_ORDER.index("projects") < DELETE_ORDER.index("vendors")


def test_work_order_prints_rupees_in_a_font_safe_way(db, client_row, vendors):
    from app.models import Project, ProjectVendor
    from app.services.work_order import work_order_terms

    project = Project(code="X-1", title="T", client_id=client_row.id, budget=1)
    pv = ProjectVendor(vendor=vendors[0], work_percent=100, agreed_cost=200000)
    cost = dict(work_order_terms(project, pv))["Agreed cost"]
    assert cost.startswith("Rs. 2,00,000")
    assert "₹" not in cost
