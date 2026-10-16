"""A proforma may not take its project past the contract value; 0 means no known cap."""

import io

from openpyxl import Workbook

from app.models import ClientInvoice, InvoiceStatus
from tests.conftest import auth_header, project_id_for
from app.models import Project


def capped_project(db, client_row, contract_value):
    project = db.get(Project, project_id_for(client_row))
    project.contract_value = contract_value
    db.commit()
    return project


def raise_pi(client, user, project_id, amount):
    return client.post(
        "/api/invoices", json={"project_id": project_id, "taxable_value": amount}, headers=auth_header(user)
    )


def test_proforma_within_the_contract_is_allowed_up_to_the_cap(client, db, users, client_row):
    project = capped_project(db, client_row, 10000)
    assert raise_pi(client, users["exec"], project.id, 6000).status_code == 201
    assert raise_pi(client, users["exec"], project.id, 4000).status_code == 201  # exactly the cap


def test_proforma_past_the_contract_is_refused_with_the_amount_over(client, db, users, client_row):
    project = capped_project(db, client_row, 10000)
    raise_pi(client, users["exec"], project.id, 6000)
    res = raise_pi(client, users["exec"], project.id, 5000)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "₹1,000.00 past its contract value of ₹10,000.00" in detail
    assert "₹4,000.00 left to bill" in detail


def test_no_contract_value_means_no_cap(client, db, users, client_row):
    project = capped_project(db, client_row, 0)
    assert raise_pi(client, users["exec"], project.id, 99_00_000).status_code == 201


def test_cancelled_invoices_free_up_the_contract(client, db, users, client_row):
    project = capped_project(db, client_row, 10000)
    first = raise_pi(client, users["exec"], project.id, 10000).json()
    db.get(ClientInvoice, first["id"]).status = InvoiceStatus.CANCELLED
    db.commit()
    assert raise_pi(client, users["exec"], project.id, 10000).status_code == 201


def test_editing_a_proforma_counts_only_the_other_invoices(client, db, users, client_row):
    project = capped_project(db, client_row, 10000)
    inv = raise_pi(client, users["exec"], project.id, 8000).json()
    url = f"/api/invoices/{inv['id']}"
    headers = auth_header(users["exec"])
    assert client.patch(url, json={"taxable_value": 10000}, headers=headers).status_code == 200
    res = client.patch(url, json={"taxable_value": 10001}, headers=headers)
    assert res.status_code == 422
    assert db.get(ClientInvoice, inv["id"]).taxable_value == 10000  # the refused edit changed nothing


def test_bulk_upload_counts_the_rows_above_on_the_same_project(client, db, users, client_row):
    project = capped_project(db, client_row, 10000)
    wb = Workbook()
    ws = wb.active
    ws.append(["Project", "Amount"])
    for amount in (6000, 4000, 1):
        ws.append([project.code, amount])
    buf = io.BytesIO()
    wb.save(buf)
    files = {"file": ("invoices.xlsx", buf.getvalue(), "application/octet-stream")}
    body = client.post("/api/invoices/bulk/preview", files=files, headers=auth_header(users["exec"])).json()
    assert [bool(r["errors"]) for r in body["rows"]] == [False, False, True]
    assert "past its contract value" in body["rows"][2]["errors"][0]
