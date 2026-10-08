"""Proforma -> client approval -> manager issue -> tax invoice."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models import InvoiceLink
from app.services.numbering import business_date, financial_year
from tests.conftest import auth_header, default_service_id

FY = financial_year(business_date())


def create_proforma(client, user, client_row, **overrides):
    body = {
        "client_id": client_row.id,
        "service_id": default_service_id(),
        "service_description": "Monthly finance retainer",
        "taxable_value": 100000,
        "gst_rate": 18,
        "sac_code": "998311",
        **overrides,
    }
    res = client.post("/api/invoices", json=body, headers=auth_header(user))
    assert res.status_code == 201, res.text
    return res.json()


def send(client, user, invoice_id):
    res = client.post(f"/api/invoices/{invoice_id}/send-to-client", headers=auth_header(user))
    assert res.status_code == 200, res.text
    return res.json()


def token_from(link_url: str) -> str:
    return link_url.rsplit("/", 1)[-1]


def approve_via_link(client, invoice_id, sent):
    res = client.post(f"/api/public/invoices/{token_from(sent['link_url'])}/approve", json={"name": "R. Shah"})
    assert res.status_code == 200, res.text
    return res.json()


def client_approved(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    approve_via_link(client, inv["id"], send(client, users["exec"], inv["id"]))
    return inv


# ---------- Proforma creation ----------


def test_new_invoice_is_a_proforma_draft_with_its_own_number(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    assert inv["status"] == "Draft"
    assert inv["proforma_number"] == f"PI/{FY}/0001"
    assert inv["invoice_number"] is None
    assert inv["invoice_date"] is None
    assert inv["display_number"] == inv["proforma_number"]
    assert inv["is_issued"] is False
    assert inv["sac_code"] == "998311"


def test_create_ignores_client_supplied_status_and_number(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row, status="Sent", invoice_number="INV-HACK-1")
    assert inv["status"] == "Draft"
    assert inv["invoice_number"] is None


def test_patch_cannot_jump_to_a_workflow_stage(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    res = client.patch(f"/api/invoices/{inv['id']}", json={"status": "Sent"}, headers=auth_header(users["ceo"]))
    assert res.status_code == 400


# ---------- Sending and client approval by link ----------


def test_send_to_client_creates_an_approval_link(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    sent = send(client, users["exec"], inv["id"])
    assert sent["invoice"]["status"] == "Awaiting Client"
    assert sent["link_url"].startswith("http://testserver/i/")
    assert sent["emailed"] is False  # SMTP is not configured in tests

    view = client.get(f"/api/public/invoices/{token_from(sent['link_url'])}")
    assert view.status_code == 200
    body = view.json()
    assert body["purpose"] == "approve"
    assert body["can_act"] is True
    assert body["invoice"]["display_number"] == inv["proforma_number"]
    assert body["company"]["legal_name"] == "Test Supplier Pvt Ltd"
    assert "internal_remarks" not in body["invoice"]


def test_client_approves_via_link_once(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    sent = send(client, users["exec"], inv["id"])
    approved = approve_via_link(client, inv["id"], sent)
    assert approved["status"] == "Client Approved"

    detail = client.get(f"/api/invoices/{inv['id']}", headers=auth_header(users["exec"])).json()
    assert detail["status"] == "Client Approved"
    assert detail["client_approval_method"] == "link"
    assert detail["client_approver_name"] == "R. Shah"
    assert detail["client_approved_at"] is not None

    again = client.post(f"/api/public/invoices/{token_from(sent['link_url'])}/approve", json={"name": "R. Shah"})
    assert again.status_code == 404


def test_approval_requires_a_name(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    sent = send(client, users["exec"], inv["id"])
    res = client.post(f"/api/public/invoices/{token_from(sent['link_url'])}/approve", json={"name": "  "})
    assert res.status_code == 422


def test_client_requests_changes(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    sent = send(client, users["exec"], inv["id"])
    res = client.post(
        f"/api/public/invoices/{token_from(sent['link_url'])}/request-changes",
        json={"comment": "Please split the retainer into two line items."},
    )
    assert res.status_code == 200, res.text
    detail = client.get(f"/api/invoices/{inv['id']}", headers=auth_header(users["exec"])).json()
    assert detail["status"] == "Changes Requested"
    assert detail["change_request_note"] == "Please split the retainer into two line items."
    assert detail["change_requested_by"] == "Client"


def test_editing_while_awaiting_client_reverts_to_draft_and_kills_the_link(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    sent = send(client, users["exec"], inv["id"])
    res = client.patch(f"/api/invoices/{inv['id']}", json={"taxable_value": 90000}, headers=auth_header(users["exec"]))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "Draft"
    assert client.get(f"/api/public/invoices/{token_from(sent['link_url'])}").status_code == 404


def test_resend_revokes_the_previous_link(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    first = send(client, users["exec"], inv["id"])
    second = send(client, users["exec"], inv["id"])
    assert first["link_url"] != second["link_url"]
    assert client.get(f"/api/public/invoices/{token_from(first['link_url'])}").status_code == 404
    assert client.get(f"/api/public/invoices/{token_from(second['link_url'])}").status_code == 200


def test_expired_link_is_rejected(client, users, client_row, db):
    inv = create_proforma(client, users["exec"], client_row)
    sent = send(client, users["exec"], inv["id"])
    link = db.query(InvoiceLink).filter(InvoiceLink.token == token_from(sent["link_url"])).one()
    link.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert client.get(f"/api/public/invoices/{link.token}").status_code == 404


def test_unknown_token_is_404(client):
    assert client.get("/api/public/invoices/not-a-real-token").status_code == 404


# ---------- Manual client approval ----------


def test_manual_client_approval_needs_a_note(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    res = client.post(f"/api/invoices/{inv['id']}/client-approval", json={"note": ""}, headers=auth_header(users["exec"]))
    assert res.status_code == 422


def test_manual_client_approval_revokes_open_links(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    sent = send(client, users["exec"], inv["id"])
    res = client.post(
        f"/api/invoices/{inv['id']}/client-approval",
        json={"note": "Approved on WhatsApp by R. Shah"},
        headers=auth_header(users["exec"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "Client Approved"
    assert body["client_approval_method"] == "manual"
    assert body["client_approval_note"] == "Approved on WhatsApp by R. Shah"
    assert client.get(f"/api/public/invoices/{token_from(sent['link_url'])}").status_code == 404


def test_client_approved_invoice_is_frozen(client, users, client_row):
    inv = client_approved(client, users, client_row)
    res = client.patch(f"/api/invoices/{inv['id']}", json={"taxable_value": 1}, headers=auth_header(users["exec"]))
    assert res.status_code == 409


# ---------- Manager issue ----------


def test_executive_cannot_issue(client, users, client_row):
    inv = client_approved(client, users, client_row)
    res = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["exec"]))
    assert res.status_code == 403


def test_creator_cannot_issue_their_own_invoice(client, users, client_row):
    inv = create_proforma(client, users["manager"], client_row)
    approve_via_link(client, inv["id"], send(client, users["manager"], inv["id"]))
    res = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    assert res.status_code == 403
    assert "created" in res.json()["detail"].lower()


def test_manager_issues_tax_invoice(client, users, client_row):
    inv = client_approved(client, users, client_row)
    res = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    body = res.json()
    issued = body["invoice"]
    assert issued["status"] == "Sent"
    assert issued["invoice_number"] == f"INV/{FY}/0001"
    assert issued["invoice_date"] == business_date().isoformat()
    assert issued["display_number"] == issued["invoice_number"]
    assert issued["proforma_number"] == inv["proforma_number"]
    assert issued["is_issued"] is True
    assert issued["is_locked"] is True
    assert issued["issued_at"] is not None

    view = client.get(f"/api/public/invoices/{token_from(body['link_url'])}").json()
    assert view["purpose"] == "view"
    assert view["can_act"] is False
    assert view["invoice"]["display_number"] == issued["invoice_number"]


def test_issue_only_from_client_approved(client, users, client_row):
    inv = create_proforma(client, users["exec"], client_row)
    res = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    assert res.status_code == 409


def test_issue_is_refused_without_company_gst_details(client, users, client_row, monkeypatch):
    inv = client_approved(client, users, client_row)
    monkeypatch.setattr(settings, "company_gstin", "")
    res = client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    assert res.status_code == 400
    assert "COMPANY_GSTIN" in res.json()["detail"]
    detail = client.get(f"/api/invoices/{inv['id']}", headers=auth_header(users["exec"])).json()
    assert detail["invoice_number"] is None


def test_returned_invoice_does_not_consume_a_number(client, users, client_row):
    first = client_approved(client, users, client_row)
    res = client.post(
        f"/api/invoices/{first['id']}/return", json={"reason": "Wrong SAC code"}, headers=auth_header(users["manager"])
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "Changes Requested"
    assert res.json()["change_request_note"] == "Wrong SAC code"
    assert res.json()["change_requested_by"] == users["manager"].name

    second = client_approved(client, users, client_row)
    issued = client.post(f"/api/invoices/{second['id']}/issue", headers=auth_header(users["cfo"])).json()
    assert issued["invoice"]["invoice_number"] == f"INV/{FY}/0001"


def test_return_needs_a_reason(client, users, client_row):
    inv = client_approved(client, users, client_row)
    res = client.post(f"/api/invoices/{inv['id']}/return", json={"reason": " "}, headers=auth_header(users["manager"]))
    assert res.status_code == 422


# ---------- Effects on payments, totals and history ----------


def test_payments_only_on_issued_invoices(client, users, client_row):
    inv = client_approved(client, users, client_row)
    payment = {"amount": 1000, "payment_date": business_date().isoformat(), "bank_name": "HDFC Bank"}
    res = client.post(f"/api/invoices/{inv['id']}/payments", json=payment, headers=auth_header(users["exec"]))
    assert res.status_code == 400

    client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    res = client.post(f"/api/invoices/{inv['id']}/payments", json=payment, headers=auth_header(users["exec"]))
    assert res.status_code == 201, res.text


def test_proformas_are_excluded_from_receivables_and_gst(client, users, client_row):
    create_proforma(client, users["exec"], client_row)
    issued = client_approved(client, users, client_row)
    client.post(f"/api/invoices/{issued['id']}/issue", headers=auth_header(users["manager"]))

    summary = client.get("/api/dashboard/summary", headers=auth_header(users["ceo"])).json()
    assert summary["net_receivable"] == pytest.approx(118000)
    assert summary["gst_payable"] == pytest.approx(18000)

    tax = client.get("/api/taxation/summary", headers=auth_header(users["ceo"])).json()
    assert tax["output_gst"]["total"] == pytest.approx(18000)
    assert tax["sales_count"] == 1
    sales = client.get("/api/taxation/gst/sales", headers=auth_header(users["ceo"])).json()
    assert [r["invoice_number"] for r in sales] == [f"INV/{FY}/0001"]


def test_timeline_records_each_step(client, users, client_row):
    inv = client_approved(client, users, client_row)
    client.post(f"/api/invoices/{inv['id']}/issue", headers=auth_header(users["manager"]))
    timeline = client.get(f"/api/invoices/{inv['id']}/timeline", headers=auth_header(users["exec"])).json()
    actions = [e["action"] for e in timeline]
    assert actions == ["Created proforma", "Sent proforma to client", "Client approved proforma", "Issued tax invoice"]
    assert timeline[2]["actor_name"] == "R. Shah (client)"


def test_erase_registry_covers_new_tables():
    from app.services.wipe import DELETE_ORDER, RETAINED, assert_covers_schema

    assert_covers_schema()
    assert "invoice_links" in DELETE_ORDER
    assert "document_sequences" in RETAINED
