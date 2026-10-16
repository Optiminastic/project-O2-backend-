"""Bulk invoice upload: a spreadsheet of rows becomes draft proformas, all or nothing."""

import io
from datetime import datetime, timedelta

import pytest
from openpyxl import Workbook, load_workbook

from app.models import AuditLog, Client, ClientInvoice, Project, Service
from app.services.numbering import business_date, month_end
from app.services.agents import house_agent
from tests.conftest import DEFAULT_SERVICE_TITLE, auth_header, default_service_id

HEADER = "Project,Amount,Description,Due date,TDS %,Interstate,Remarks\n"


def csv_file(*rows: str, header: str = HEADER) -> dict:
    body = header + "".join(r + "\n" for r in rows)
    return {"file": ("invoices.csv", body.encode("utf-8"), "text/csv")}


def xlsx_file(rows: list[list]) -> dict:
    wb = Workbook()
    ws = wb.active
    ws.append(["Project", "Amount", "Description", "Due date", "TDS %", "Interstate", "Remarks"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return {"file": ("invoices.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def preview(client, user, files):
    return client.post("/api/invoices/bulk/preview", files=files, headers=auth_header(user))


def commit(client, user, files):
    return client.post("/api/invoices/bulk", files=files, headers=auth_header(user))


def add_project(db, code, client_row, service_id=None):
    service_id = service_id or default_service_id()
    project = Project(code=code, title=f"{code} work", client_id=client_row.id, service_id=service_id)
    project.services = [db.get(Service, service_id)]
    db.add(project)
    db.commit()
    return project


@pytest.fixture
def acme(db, client_row):
    """Acme's retainer project on the default (18%) service."""
    return add_project(db, "ACME-RET", client_row)


def second_client(db, agent_id=None):
    row = Client(business_name="Bharat Steel", email="ap@bharat.example", gst_number="29CCCCC2222C1Z5",
                 agent_id=agent_id or house_agent(db).id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_preview_totals_rows_for_several_projects(client, users, acme, db):
    add_project(db, "BHARAT-RET", second_client(db))
    res = preview(client, users["exec"], csv_file(
        "ACME-RET,10000,,,,,",
        'BHARAT-RET,"1,00,000",Q3 retainer,,,yes,',
    ))
    assert res.status_code == 200, res.text
    p = res.json()
    assert p["ready"] is True
    assert p["invoice_count"] == 2
    assert p["client_count"] == 2
    assert p["taxable_total"] == 110000
    assert p["gst_total"] == 19800  # the service is 18%
    assert p["grand_total"] == 129800
    first, second = p["rows"]
    assert first["row"] == 2 and first["project_code"] == "ACME-RET" and first["errors"] == []
    assert (first["client_name"], first["service_title"]) == ("Acme Traders", DEFAULT_SERVICE_TITLE)
    assert second["client_name"] == "Bharat Steel"
    assert second["is_interstate"] is True
    assert second["description"] == "Q3 retainer"


def test_project_codes_match_ignoring_case_and_spaces(client, users, acme):
    res = preview(client, users["exec"], csv_file("  acme-ret ,500,,,,,"))
    assert res.json()["ready"] is True


def test_every_problem_is_reported_against_its_row(client, users, acme, client_row, db):
    old = Service(title="Old service", gst_rate=18, is_active=False)
    db.add(old)
    db.commit()
    add_project(db, "OLD-SVC", client_row, old.id)
    db.add(Project(code="NO-SVC", title="No service", client_id=client_row.id))
    db.commit()
    yesterday = (business_date() - timedelta(days=1)).isoformat()
    res = preview(client, users["exec"], csv_file(
        "RELIENCE-1,1000,,,,,",
        "NO-SVC,1000,,,,,",
        "OLD-SVC,1000,,,,,",
        "ACME-RET,abc,,,,,",
        "ACME-RET,0,,,,,",
        f"ACME-RET,1000,,{yesterday},,,",
        "ACME-RET,1000,,31-02-2026,,,",
        "ACME-RET,1000,,,150,,",
        "ACME-RET,1000,,,,maybe,",
        ",1000,,,,,",
    ))
    p = res.json()
    assert p["ready"] is False
    assert p["error_count"] == 10
    errors = {r["row"]: " ".join(r["errors"]) for r in p["rows"]}
    assert "No project with code 'RELIENCE-1'" in errors[2]
    assert "has no service" in errors[3]
    assert "deactivated" in errors[4]
    assert "Amount" in errors[5]
    assert "more than zero" in errors[6]
    assert "in the past" in errors[7]
    assert "Due date" in errors[8]
    assert "TDS" in errors[9]
    assert "Interstate" in errors[10]
    assert "Project is required" in errors[11]


def test_repeated_rows_are_flagged_but_allowed(client, users, acme):
    row = "ACME-RET,1000,,,,,"
    p = preview(client, users["exec"], csv_file(row, row)).json()
    assert p["ready"] is True
    assert "Same project and amount as row 2" in p["rows"][1]["warnings"][0]


def test_upload_creates_draft_proformas_in_row_order(client, users, acme, db):
    agent_client = second_client(db)
    bharat = add_project(db, "BHARAT-RET", agent_client)
    res = commit(client, users["exec"], csv_file(
        "ACME-RET,10000,,,,,",
        "BHARAT-RET,2000,Setup fee,,2,,Per email",
    ))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["created"] == 2
    numbers = [i["proforma_number"] for i in body["invoices"]]
    assert numbers[0].endswith("/0001") and numbers[1].endswith("/0002")

    invoices = db.query(ClientInvoice).order_by(ClientInvoice.id).all()
    assert [i.status.value for i in invoices] == ["Draft", "Draft"]
    assert invoices[0].invoice_number is None  # a tax invoice number comes only at issue
    assert invoices[0].due_date == month_end(business_date())
    assert invoices[0].total_amount == 11800
    assert invoices[0].project_id == acme.id
    assert invoices[1].project_id == bharat.id
    assert invoices[1].client_id == agent_client.id
    assert invoices[1].agent_id == agent_client.agent_id
    assert invoices[1].service_description == "Setup fee"
    assert invoices[1].tds_rate == 2
    assert invoices[1].internal_remarks == "Per email"
    assert invoices[1].created_by_id == users["exec"].id
    assert db.query(AuditLog).filter(AuditLog.action == "Bulk uploaded proformas").count() == 1


def test_one_bad_row_means_nothing_is_created(client, users, acme, db):
    res = commit(client, users["exec"], csv_file("ACME-RET,10000,,,,,", "NOBODY-1,10000,,,,,"))
    assert res.status_code == 422
    assert "1 row" in res.json()["detail"]
    assert db.query(ClientInvoice).count() == 0

    # The proforma series was not consumed by the failed upload.
    good = commit(client, users["exec"], csv_file("ACME-RET,10000,,,,,"))
    assert good.json()["invoices"][0]["proforma_number"].endswith("/0001")


def test_excel_upload_with_real_date_cells(client, users, acme):
    due = month_end(business_date()) + timedelta(days=15)
    res = commit(client, users["exec"], xlsx_file([
        ["ACME-RET", 2500.5, None, datetime(due.year, due.month, due.day), 10, "No", None],
        [None, None, None, None, None, None, None],  # blank rows are ignored
    ]))
    assert res.status_code == 201, res.text
    assert res.json()["created"] == 1


def test_indian_style_dates_are_read_day_first(client, users, acme):
    due = business_date() + timedelta(days=40)
    p = preview(client, users["exec"], csv_file(f"ACME-RET,1000,,{due:%d/%m/%Y},,,")).json()
    assert p["rows"][0]["due_date"] == due.isoformat()


def test_file_shape_problems_are_rejected_up_front(client, users, acme):
    res = preview(client, users["exec"], csv_file("Acme Traders,Retainer", header="Client,Service\n"))
    assert res.status_code == 400
    assert "Project" in res.json()["detail"]

    res = preview(client, users["exec"], {"file": ("invoices.pdf", b"%PDF", "application/pdf")})
    assert res.status_code == 400

    res = preview(client, users["exec"], csv_file())
    assert res.status_code == 400
    assert "no invoice rows" in res.json()["detail"]

    many = ["ACME-RET,1000,,,,," for _ in range(501)]
    res = preview(client, users["exec"], csv_file(*many))
    assert res.status_code == 400
    assert "500" in res.json()["detail"]


def test_template_lists_invoiceable_projects(client, users, acme, client_row, db):
    db.add(Project(code="NO-SVC", title="No service", client_id=client_row.id))
    db.commit()
    res = client.get("/api/invoices/bulk/template", headers=auth_header(users["exec"]))
    assert res.status_code == 200
    wb = load_workbook(io.BytesIO(res.content))
    headers = [c.value for c in wb["Invoices"][1]]
    assert headers[:2] == ["Project", "Amount"]
    codes = [c.value for c in wb["Lists"]["A"]]
    assert "ACME-RET" in codes
    assert "NO-SVC" not in codes  # cannot be invoiced yet


def test_bulk_upload_needs_a_finance_login(client):
    assert client.post("/api/invoices/bulk/preview", files=csv_file()).status_code == 401
