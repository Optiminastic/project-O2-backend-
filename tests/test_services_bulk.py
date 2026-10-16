"""Bulk service upload: a spreadsheet of services, all or nothing; a shared SAC is only a warning."""

import io

from openpyxl import Workbook, load_workbook

from app.models import AuditLog, Service
from tests.conftest import DEFAULT_SERVICE_TITLE, auth_header

HEADER = "Title,SAC code\n"


def csv_file(*rows: str, header: str = HEADER) -> dict:
    body = header + "".join(r + "\n" for r in rows)
    return {"file": ("services.csv", body.encode("utf-8"), "text/csv")}


def xlsx_file(rows: list[list]) -> dict:
    wb = Workbook()
    ws = wb.active
    ws.append(["Title", "SAC code"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return {"file": ("services.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def preview(client, user, files):
    return client.post("/api/services/bulk/preview", files=files, headers=auth_header(user))


def commit(client, user, files):
    return client.post("/api/services/bulk", files=files, headers=auth_header(user))


def test_clean_file_previews_ready_and_creates_every_service(client, db, users):
    files = csv_file("SEO audit,998365", "Social media management,998366", "Brand workshop,")
    res = preview(client, users["manager"], files)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ready"] is True
    assert body["service_count"] == 3
    assert [r["sac_code"] for r in body["rows"]] == ["998365", "998366", None]

    res = commit(client, users["manager"], files)
    assert res.status_code == 201, res.text
    assert res.json()["created"] == 3
    titles = {s.title for s in db.query(Service).all()}
    assert {"SEO audit", "Social media management", "Brand workshop"} <= titles
    assert db.query(AuditLog).filter(AuditLog.action == "Bulk uploaded services").count() == 1


def test_shared_sac_is_a_warning_not_an_error(client, users):
    # 998311 belongs to the seeded service; the second row repeats the first row's SAC.
    body = preview(client, users["manager"], csv_file("Tax advisory,998311", "Payroll,998365", "Compliance,998365")).json()
    assert body["ready"] is True
    first, _, third = body["rows"]
    assert any(DEFAULT_SERVICE_TITLE in w for w in first["warnings"])
    assert any("row 3" in w for w in third["warnings"])


def test_existing_or_repeated_titles_block_the_whole_upload(client, db, users):
    before = db.query(Service).count()
    files = csv_file(f"{DEFAULT_SERVICE_TITLE.upper()},998365", "New thing,", "new  thing,")
    body = preview(client, users["manager"], files).json()
    assert body["ready"] is False
    assert body["error_count"] == 2
    assert "already exists" in body["rows"][0]["errors"][0]
    assert "row 3" in body["rows"][2]["errors"][0]

    res = commit(client, users["manager"], files)
    assert res.status_code == 422
    assert "2 rows" in res.json()["detail"]
    assert db.query(Service).count() == before


def test_bad_sac_and_missing_title_are_errors(client, users):
    body = preview(client, users["manager"], csv_file(",998365", "Short SAC,123", "Letters,99A1")).json()
    errors = [r["errors"] for r in body["rows"]]
    assert "Title is required." in errors[0]
    assert all("4 to 8 digits" in e[0] for e in errors[1:])


def test_excel_number_cells_keep_the_sac_digits(client, users):
    body = preview(client, users["manager"], xlsx_file([["Video production", 999613], ["Design", 998391.0]])).json()
    assert body["ready"] is True
    assert [r["sac_code"] for r in body["rows"]] == ["999613", "998391"]


def test_file_problems_are_reported_plainly(client, users):
    assert "Title" in preview(client, users["manager"], csv_file("x", header="Name\n")).json()["detail"]
    assert "no service rows" in preview(client, users["manager"], csv_file()).json()["detail"]
    files = {"file": ("services.txt", b"Title\nX\n", "text/plain")}
    assert preview(client, users["manager"], files).status_code == 400


def test_executive_cannot_bulk_upload_services(client, users):
    assert preview(client, users["exec"], csv_file("X,998365")).status_code == 403
    assert commit(client, users["exec"], csv_file("X,998365")).status_code == 403


def test_template_has_the_header_row(client, users):
    res = client.get("/api/services/bulk/template", headers=auth_header(users["manager"]))
    assert res.status_code == 200
    wb = load_workbook(io.BytesIO(res.content))
    assert [c.value for c in wb["Services"][1]] == ["Title", "SAC code"]
