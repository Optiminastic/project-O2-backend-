"""Projects: one client project, vendors assigned per service under permanent codes, a budget, and work orders."""

from datetime import date

import pytest
from sqlalchemy import text

from app.database import engine
from app.models import AuditLog, Service, Vendor, VendorAllocation, VendorInvoice
from app.services.numbering import business_date, financial_year, month_end
from app.services.projects import default_report_date
from tests.conftest import auth_header, default_service_id


@pytest.fixture
def vendors(db):
    """Two vendors providing the default service; Field Force also does data collection."""
    service = db.get(Service, default_service_id())
    collection = Service(title=COLLECTION, sac_code="998372", gst_rate=18)
    db.add(collection)
    rows = [
        Vendor(business_name="Quanta Research", email="ops@quanta.example", services=[service]),
        Vendor(business_name="Field Force", email="hello@fieldforce.example", services=[service, collection]),
    ]
    db.add_all(rows)
    db.commit()
    for v in rows:
        db.refresh(v)
    return rows


@pytest.fixture
def second_service(db, vendors):
    """A second service on the catalogue, provided by the first vendor only."""
    s = Service(title="Field survey", sac_code="998371", gst_rate=18)
    db.add(s)
    vendors[0].services.append(s)
    db.commit()
    db.refresh(s)
    return s


COLLECTION = "Data collection"


def collection_id():
    from app.database import SessionLocal

    with SessionLocal() as session:
        return session.query(Service).filter(Service.title == COLLECTION).one().id


def share(vendor, cost, service_id=None):
    return {"vendor_id": vendor.id, "services": [{"service_id": service_id or default_service_id(), "agreed_cost": cost}]}


def project_body(client_row, vendors, **overrides):
    body = {
        "code": "axis-q4",
        "title": "Axis Q4 market study",
        "brand": "Axis Bank",
        "description": "Survey 2,000 customers across 8 cities.",
        "client_id": client_row.id,
        "service_ids": [default_service_id(), collection_id()],
        "budget": 500000,
        "start_date": "2026-10-01",
        "end_date": "2026-11-20",
        "vendors": [share(vendors[0], 240000), share(vendors[1], 160000, collection_id())],
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
    # Report is due on the 25th of the month after the start month unless chosen.
    assert p["expected_report_date"] == "2026-11-25"
    assert db_audit(client, "Created project")


def db_audit(client, action):
    from app.database import SessionLocal

    with SessionLocal() as s:
        return s.query(AuditLog).filter(AuditLog.action == action).count() == 1


def test_report_date_can_be_chosen(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors, expected_report_date="2026-12-05")).json()
    assert p["expected_report_date"] == "2026-12-05"


def test_report_date_without_a_start_is_the_25th_of_next_month(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors, start_date=None, end_date=None)).json()
    assert p["expected_report_date"] == default_report_date(business_date()).isoformat()


def test_start_month_is_kept_as_its_first_day(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors, start_date="2026-12-17", end_date=None)).json()
    assert p["start_date"] == "2026-12-01"
    assert p["expected_report_date"] == "2027-01-25"


def test_code_is_generated_when_left_out(client, users, client_row, vendors):
    first = create(client, users["manager"], project_body(client_row, vendors, code=None)).json()
    second_body = project_body(client_row, vendors, code=None)
    second_body["vendors"] = []
    second = create(client, users["manager"], second_body).json()
    assert first["code"].startswith("PRJ-") and second["code"].startswith("PRJ-")
    assert first["code"] != second["code"]


def test_each_vendor_service_gets_a_permanent_code(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    codes = [[s["code"] for s in v["services"]] for v in p["vendors"]]
    assert codes == [["AXIS-Q4-V01-S01"], ["AXIS-Q4-V02-S01"]]
    assert [v["agreed_cost"] for v in p["vendors"]] == [240000, 160000]


def test_a_vendor_only_delivers_services_it_provides_on_the_project(client, users, client_row, vendors, second_service):
    body = project_body(client_row, vendors)
    # Field survey is not a service on this project.
    body["vendors"] = [share(vendors[0], 1000, second_service.id)]
    res = create(client, users["manager"], body)
    assert res.status_code == 400 and "not a service on this project" in res.json()["detail"]
    # On the project, but Field Force does not provide it.
    body = project_body(client_row, vendors, service_ids=[default_service_id(), second_service.id])
    body["vendors"] = [share(vendors[1], 1000, second_service.id)]
    res = create(client, users["manager"], body)
    assert res.status_code == 400 and "does not provide Field survey" in res.json()["detail"]


def test_assigning_services_keeps_codes_and_never_reuses_them(client, users, client_row, vendors, second_service):
    body = project_body(client_row, vendors, service_ids=[default_service_id(), second_service.id])
    body["vendors"] = [share(vendors[0], 1000)]
    p = create(client, users["manager"], body).json()
    url, h = f"/api/projects/{p['id']}/vendors/{vendors[0].id}", auth_header(users["manager"])
    both = {"services": [{"service_id": default_service_id(), "agreed_cost": 1000},
                         {"service_id": second_service.id, "agreed_cost": 500}]}
    res = client.put(url, json=both, headers=h)
    assert res.status_code == 200, res.text
    (row,) = res.json()["vendors"]
    assert [s["code"] for s in row["services"]] == ["AXIS-Q4-V01-S01", "AXIS-Q4-V01-S02"]
    assert row["agreed_cost"] == 1500
    # Drop the survey, then bring it back: it gets a new code, S03.
    client.put(url, json={"services": both["services"][:1]}, headers=h)
    again = client.put(url, json=both, headers=h).json()["vendors"][0]
    assert [s["code"] for s in again["services"]] == ["AXIS-Q4-V01-S01", "AXIS-Q4-V01-S03"]
    # A removed vendor's number is not reused either.
    client.delete(url, headers=h)
    added = client.put(f"/api/projects/{p['id']}/vendors/{vendors[1].id}", json={"services": both["services"][:1]}, headers=h)
    assert added.json()["vendors"][0]["services"][0]["code"] == "AXIS-Q4-V02-S01"


def test_a_service_cannot_leave_the_project_while_a_vendor_delivers_it(client, users, client_row, vendors, second_service):
    body = project_body(client_row, vendors, service_ids=[default_service_id(), second_service.id])
    body["vendors"] = [share(vendors[0], 1000, second_service.id)]
    p = create(client, users["manager"], body).json()
    res = client.patch(f"/api/projects/{p['id']}", json={"service_ids": [default_service_id()]},
                       headers=auth_header(users["manager"]))
    assert res.status_code == 400 and "Field survey" in res.json()["detail"]


def test_vendor_services_are_edited_on_the_vendor(client, users, client_row, vendors, second_service):
    url, h = f"/api/vendors/{vendors[1].id}/services", auth_header(users["manager"])
    res = client.put(url, json={"service_ids": [default_service_id(), second_service.id]}, headers=h)
    assert res.status_code == 200, res.text
    assert [s["title"] for s in res.json()["services"]] == ["Field survey", "Finance retainer"]
    assert client.put(url, json={"service_ids": []}, headers=h).json()["services"] == []
    assert client.put(url, json={"service_ids": [default_service_id()]}, headers=auth_header(users["exec"])).status_code == 403


def test_a_vendor_cannot_drop_a_service_it_is_assigned(client, users, client_row, vendors):
    create(client, users["manager"], project_body(client_row, vendors))
    res = client.put(f"/api/vendors/{vendors[0].id}/services", json={"service_ids": []}, headers=auth_header(users["manager"]))
    assert res.status_code == 400
    assert "AXIS-Q4-V01-S01" in res.json()["detail"]


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


def test_vendors_cannot_go_past_the_vendor_budget(client, users, client_row, vendors):
    res = create(client, users["manager"], project_body(client_row, vendors, budget=350000))
    assert res.status_code == 400 and "vendor budget" in res.json()["detail"]
    # Exactly 100% allocated is fine; then no more cost can be added.
    p = create(client, users["manager"], project_body(client_row, vendors, budget=400000)).json()
    assert (p["committed_cost"], p["remaining_budget"]) == (400000, 0)
    url, h = f"/api/projects/{p['id']}/vendors/{vendors[1].id}", auth_header(users["manager"])
    more = {"services": [{"service_id": collection_id(), "agreed_cost": 160001}]}
    assert client.put(url, json=more, headers=h).status_code == 400
    # Lowering a cost is always allowed.
    less = {"services": [{"service_id": collection_id(), "agreed_cost": 100000}]}
    assert client.put(url, json=less, headers=h).status_code == 200


def test_a_project_without_a_budget_has_no_cap(client, users, client_row, vendors):
    res = create(client, users["manager"], project_body(client_row, vendors, budget=0))
    assert res.status_code == 201, res.text


def test_each_service_has_one_vendor(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    h = auth_header(users["manager"])
    # Field Force cannot also take Quanta's service.
    clash = {"services": [{"service_id": collection_id(), "agreed_cost": 160000},
                          {"service_id": default_service_id(), "agreed_cost": 1000}]}
    res = client.put(f"/api/projects/{p['id']}/vendors/{vendors[1].id}", json=clash, headers=h)
    assert res.status_code == 400 and "already has a vendor" in res.json()["detail"]
    both = {"vendors": [share(vendors[0], 1000), share(vendors[1], 1000)]}
    assert client.patch(f"/api/projects/{p['id']}", json=both, headers=h).status_code == 400
    # Moving the service from one vendor to the other in one save is fine.
    moved = {"vendors": [share(vendors[1], 1000)]}
    res = client.patch(f"/api/projects/{p['id']}", json=moved, headers=h)
    assert res.status_code == 200, res.text


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

    res = client.patch(f"/api/projects/{p['id']}", json={"vendors": [share(vendors[0], 280000)]}, headers=h)
    assert res.status_code == 200, res.text
    (row,) = res.json()["vendors"]
    assert row["services"][0]["code"] == "AXIS-Q4-V01-S01"
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


def code_id(project, vendor_index, service_index=0):
    return project["vendors"][vendor_index]["services"][service_index]["id"]


def record_vendor_invoice(client, user, vendor_id, lines, number=None):
    """``lines``: (assignment id, amount) pairs."""
    return client.post("/api/vendors/invoices", json={
        "vendor_id": vendor_id, "invoice_number": number or f"V-{int(sum(a for _, a in lines))}",
        "invoice_date": business_date().isoformat(),
        "lines": [{"assignment_id": i, "amount": a} for i, a in lines],
    }, headers=auth_header(user))


def test_vendor_invoices_count_against_the_project(client, users, client_row, vendors, db):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    res = record_vendor_invoice(client, users["exec"], vendors[0].id, [(code_id(p, 0), 250000)])
    assert res.status_code == 201, res.text
    bill = res.json()
    assert (bill["project_id"], bill["invoice_amount"]) == (p["id"], 250000)
    assert [(line["code"], line["amount"]) for line in bill["lines"]] == [("AXIS-Q4-V01-S01", 250000)]
    detail = client.get(f"/api/projects/{p['id']}", headers=auth_header(users["exec"])).json()
    assert detail["invoiced_cost"] == 250000
    first = detail["vendors"][0]
    assert first["invoiced"] == 250000
    assert first["over_invoiced"] is True  # agreed 2,40,000
    assert (first["services"][0]["invoiced"], first["services"][0]["over_invoiced"]) == (250000, True)
    assert [i["invoice_number"] for i in detail["vendor_invoices"]] == ["V-250000"]


def test_a_bill_charges_only_the_vendors_own_codes_on_one_project(client, users, client_row, vendors, db):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    other = create(client, users["manager"], project_body(client_row, vendors, code="other-1")).json()
    exec_ = users["exec"]
    # Field Force's code on a Quanta bill.
    assert record_vendor_invoice(client, exec_, vendors[0].id, [(code_id(p, 1), 100)]).status_code == 400
    # Two projects on one bill.
    assert record_vendor_invoice(client, exec_, vendors[0].id, [(code_id(p, 0), 100), (code_id(other, 0), 100)]).status_code == 400
    # The same code twice, an unknown code, and no code at all.
    assert record_vendor_invoice(client, exec_, vendors[0].id, [(code_id(p, 0), 100), (code_id(p, 0), 100)]).status_code == 400
    assert record_vendor_invoice(client, exec_, vendors[0].id, [(999999, 100)]).status_code == 404
    assert record_vendor_invoice(client, exec_, vendors[0].id, []).status_code == 422


def test_a_bill_can_split_across_the_vendors_services(client, users, client_row, vendors, second_service):
    body = project_body(client_row, vendors, service_ids=[default_service_id(), second_service.id])
    body["vendors"] = [{"vendor_id": vendors[0].id, "services": [
        {"service_id": default_service_id(), "agreed_cost": 1000}, {"service_id": second_service.id, "agreed_cost": 500},
    ]}]
    p = create(client, users["manager"], body).json()
    res = record_vendor_invoice(client, users["exec"], vendors[0].id, [(code_id(p, 0, 0), 600), (code_id(p, 0, 1), 500)])
    assert res.status_code == 201, res.text
    assert res.json()["invoice_amount"] == 1100
    assignments = client.get(f"/api/vendors/{vendors[0].id}/assignments", headers=auth_header(users["exec"])).json()
    assert [(a["code"], a["invoiced"], a["remaining"]) for a in assignments] == [
        ("AXIS-Q4-V01-S01", 600, 400), ("AXIS-Q4-V01-S02", 500, 0),
    ]
    # A billed service cannot be taken off the vendor.
    only_first = {"services": [{"service_id": default_service_id(), "agreed_cost": 1000}]}
    res = client.put(f"/api/projects/{p['id']}/vendors/{vendors[0].id}", json=only_first, headers=auth_header(users["manager"]))
    assert res.status_code == 400 and "already billed Field survey" in res.json()["detail"]


def test_margin_is_shown_per_service(client, users, client_row, vendors, db):
    from app.models import ClientInvoice, InvoiceLine, InvoiceStatus

    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    record_vendor_invoice(client, users["exec"], vendors[0].id, [(code_id(p, 0), 200000)])
    inv = ClientInvoice(client_id=client_row.id, agent_id=client_row.agent_id, project_id=p["id"],
                        service_id=default_service_id(), status=InvoiceStatus.SENT, invoice_number="INV/X",
                        invoice_date=business_date(), taxable_value=500000, total_amount=590000)
    inv.lines = [InvoiceLine(service_id=default_service_id(), service_title="Finance retainer", taxable_value=500000)]
    db.add(inv)
    db.commit()
    rows = client.get(f"/api/projects/{p['id']}", headers=auth_header(users["exec"])).json()["service_margins"]
    row = next(r for r in rows if r["service_id"] == default_service_id())
    assert (row["billed"], row["vendor_committed"], row["vendor_invoiced"], row["margin"]) == (500000, 240000, 200000, 300000)


def test_vendor_with_invoices_cannot_be_removed_and_project_cannot_be_deleted(client, users, client_row, vendors):
    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    record_vendor_invoice(client, users["exec"], vendors[1].id, [(code_id(p, 1), 100000)])
    h = auth_header(users["manager"])
    assert client.delete(f"/api/projects/{p['id']}/vendors/{vendors[1].id}", headers=h).status_code == 400
    res = client.patch(f"/api/projects/{p['id']}", json={"vendors": [share(vendors[0], 240000)]}, headers=h)
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
    pv = ProjectVendor(vendor=vendors[0], agreed_cost=200000)
    cost = dict(work_order_terms(project, pv))["Agreed cost"]
    assert cost.startswith("Rs. 2,00,000")
    assert "₹" not in cost


def test_work_order_lists_each_service_with_its_code(client, users, client_row, vendors):
    from app.database import SessionLocal
    from app.models import ProjectVendor
    from app.services.work_order import work_order_services

    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    res = client.get(f"/api/projects/{p['id']}/vendors/{vendors[0].id}/work-order.pdf", headers=auth_header(users["manager"]))
    assert res.status_code == 200 and res.content.startswith(b"%PDF")
    with SessionLocal() as session:
        pv = session.query(ProjectVendor).filter_by(project_id=p["id"], vendor_id=vendors[0].id).one()
        assert work_order_services(pv) == [("AXIS-Q4-V01-S01", "Finance retainer", "998311", "Rs. 2,40,000.00")]


def test_existing_vendors_on_projects_become_coded_services(db, client_row):
    """The upgrade: each vendor on a project gets the project's first service, coded, and its old bills move onto it."""
    from app.models import Project, ProjectVendor, VendorInvoiceLine
    from app.schema_upgrades import PROJECT_VENDOR_SERVICES, _run

    v1 = Vendor(business_name="Old One", email="one@old.example")
    v2 = Vendor(business_name="Old Two", email="two@old.example")
    project = Project(code="LEGACY-7", title="Old work", client_id=client_row.id, service_id=default_service_id())
    project.vendors = [ProjectVendor(vendor=v1, work_percent=60, agreed_cost=600),
                       ProjectVendor(vendor=v2, work_percent=40, agreed_cost=400)]
    db.add(project)
    db.flush()
    db.add(VendorInvoice(vendor_id=v1.id, project_id=project.id, invoice_number="OLD-9",
                         invoice_date=date(2026, 10, 1), invoice_amount=300))
    db.commit()

    _run(engine, PROJECT_VENDOR_SERVICES)
    _run(engine, PROJECT_VENDOR_SERVICES)  # idempotent

    db.expire_all()
    pvs = db.query(ProjectVendor).filter_by(project_id=project.id).order_by(ProjectVendor.id).all()
    assert [(pv.vendor_seq, [(s.code, s.agreed_cost) for s in pv.services]) for pv in pvs] == [
        (1, [("LEGACY-7-V01-S01", 600)]), (2, [("LEGACY-7-V02-S01", 400)]),
    ]
    assert db.get(Project, project.id).last_vendor_seq == 2
    assert [s.id for s in db.get(Vendor, v1.id).services] == [default_service_id()]
    (line,) = db.query(VendorInvoiceLine).all()
    assert (line.assignment.code, line.amount) == ("LEGACY-7-V01-S01", 300)


def test_project_reports_the_gst_on_what_is_billed(client, users, client_row, vendors, db):
    from app.models import ClientInvoice, InvoiceStatus

    p = create(client, users["manager"], project_body(client_row, vendors)).json()
    for number, status_, taxable in (("INV/G1", InvoiceStatus.SENT, 100000), ("INV/G2", InvoiceStatus.CANCELLED, 50000)):
        db.add(ClientInvoice(client_id=client_row.id, agent_id=client_row.agent_id, project_id=p["id"],
                             service_id=default_service_id(), status=status_, invoice_number=number,
                             invoice_date=business_date(), taxable_value=taxable, gst_amount=taxable * 0.18,
                             total_amount=taxable * 1.18))
    db.commit()
    detail = client.get(f"/api/projects/{p['id']}", headers=auth_header(users["exec"])).json()
    # Cancelled invoices do not count.
    assert (detail["billed"], detail["billed_gst"]) == (100000, 18000)
