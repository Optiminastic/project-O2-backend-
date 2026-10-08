"""?month=YYYY-MM filters lists to one Indian calendar month."""

from datetime import date, datetime, timezone

from app.core.period import month_period
from app.models import AuditLog, ClientInvoice, InvoiceStatus, Payment
from tests.conftest import auth_header, default_service_id


def add_invoice(db, client_row, *, invoice_date=None, proforma_date=None, status=InvoiceStatus.SENT, number=None):
    inv = ClientInvoice(
        client_id=client_row.id,
        agent_id=client_row.agent_id,
        service_id=default_service_id(),
        invoice_number=number,
        proforma_number=None if number else f"PI/T/{proforma_date}",
        invoice_date=invoice_date,
        proforma_date=proforma_date,
        status=status,
        taxable_value=1000,
        gst_amount=180,
        total_amount=1180,
        amount_pending=1180,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def test_month_period_covers_the_whole_indian_month():
    period = month_period("2026-02")
    assert (period.start, period.end) == (date(2026, 2, 1), date(2026, 3, 1))
    assert month_period(None) is None


def test_invalid_month_is_rejected(client, users):
    assert client.get("/api/invoices?month=2026-13", headers=auth_header(users["exec"])).status_code == 422
    assert client.get("/api/invoices?month=Oct", headers=auth_header(users["exec"])).status_code == 422


def test_invoices_filter_by_invoice_date_or_proforma_date(client, users, client_row, db):
    add_invoice(db, client_row, invoice_date=date(2026, 9, 30), number="INV/A/1")
    oct_issued = add_invoice(db, client_row, invoice_date=date(2026, 10, 1), number="INV/A/2")
    oct_proforma = add_invoice(db, client_row, proforma_date=date(2026, 10, 15), status=InvoiceStatus.DRAFT)

    rows = client.get("/api/invoices?month=2026-10", headers=auth_header(users["exec"])).json()
    assert {r["id"] for r in rows} == {oct_issued.id, oct_proforma.id}
    assert len(client.get("/api/invoices", headers=auth_header(users["exec"])).json()) == 3


def test_receipts_filter_by_payment_date(client, users, client_row, db):
    inv = add_invoice(db, client_row, invoice_date=date(2026, 9, 1), number="INV/B/1")
    db.add_all([
        Payment(invoice_id=inv.id, amount=100, payment_date=date(2026, 9, 20)),
        Payment(invoice_id=inv.id, amount=200, payment_date=date(2026, 10, 5)),
    ])
    db.commit()
    rows = client.get("/api/taxation/receipts?month=2026-10", headers=auth_header(users["manager"])).json()
    assert [r["amount"] for r in rows] == [200]


def test_taxation_summary_counts_only_that_month(client, users, client_row, db):
    add_invoice(db, client_row, invoice_date=date(2026, 9, 10), number="INV/C/1")
    add_invoice(db, client_row, invoice_date=date(2026, 10, 10), number="INV/C/2")
    summary = client.get("/api/taxation/summary?month=2026-10", headers=auth_header(users["manager"])).json()
    assert summary["sales_count"] == 1
    assert summary["output_gst"]["total"] == 180


def test_timestamped_records_use_india_time(client, users, db):
    # 19:00 UTC on 30 Sep is 00:30 IST on 1 Oct, so it belongs to October in India.
    db.add_all([
        AuditLog(action="late september", entity_type="X", created_at=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)),
        AuditLog(action="first of october", entity_type="X", created_at=datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc)),
    ])
    db.commit()
    rows = client.get("/api/audit?month=2026-10&entity_type=X", headers=auth_header(users["ceo"])).json()
    assert [r["action"] for r in rows] == ["first of october"]
