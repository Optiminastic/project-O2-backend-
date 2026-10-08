"""Idempotent schema upgrades applied at startup.

Production does not run Alembic on deploy, and ``create_all()`` only creates
missing tables, never new columns or enum values. Each statement here is safe
to run on every start. Keep in step with the matching Alembic revision.
"""

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

logger = logging.getLogger("o2.schema")

# Matches alembic revision d4a7c2e9f1b8 (invoice approval workflow).
INVOICE_WORKFLOW = (
    "ALTER TYPE invoicestatus ADD VALUE IF NOT EXISTS 'AWAITING_CLIENT'",
    "ALTER TYPE invoicestatus ADD VALUE IF NOT EXISTS 'CHANGES_REQUESTED'",
    "ALTER TYPE invoicestatus ADD VALUE IF NOT EXISTS 'CLIENT_APPROVED'",
    """
    DO $$ BEGIN
        CREATE TYPE clientapprovalmethod AS ENUM ('LINK', 'MANUAL');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$
    """,
    """
    ALTER TABLE client_invoices
        ADD COLUMN IF NOT EXISTS proforma_number VARCHAR(60),
        ADD COLUMN IF NOT EXISTS proforma_date DATE,
        ADD COLUMN IF NOT EXISTS sac_code VARCHAR(12),
        ADD COLUMN IF NOT EXISTS created_by_id INTEGER REFERENCES users(id),
        ADD COLUMN IF NOT EXISTS client_approved_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS client_approval_method clientapprovalmethod,
        ADD COLUMN IF NOT EXISTS client_approver_name VARCHAR(160),
        ADD COLUMN IF NOT EXISTS client_approval_note TEXT,
        ADD COLUMN IF NOT EXISTS change_request_note TEXT,
        ADD COLUMN IF NOT EXISTS change_requested_by VARCHAR(160),
        ADD COLUMN IF NOT EXISTS issued_by_id INTEGER REFERENCES users(id),
        ADD COLUMN IF NOT EXISTS issued_at TIMESTAMPTZ
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_client_invoices_proforma_number ON client_invoices (proforma_number)",
    "ALTER TABLE client_invoices ALTER COLUMN invoice_number DROP NOT NULL",
    "ALTER TABLE client_invoices ALTER COLUMN invoice_date DROP NOT NULL",
)


# Matches alembic revision e8b3f5a1c7d2 (mandatory agents). Runs in two parts
# around creating the in-house agent, which the backfill needs.
AGENTS_COLUMNS = (
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_house BOOLEAN NOT NULL DEFAULT false",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_house ON agents (is_house) WHERE is_house",
)
AGENTS_BACKFILL = (
    # Records with no agent came in directly: credit the in-house agent (no commission).
    "UPDATE clients SET agent_id = (SELECT id FROM agents WHERE is_house) WHERE agent_id IS NULL",
    "UPDATE client_invoices SET agent_id = (SELECT id FROM agents WHERE is_house) WHERE agent_id IS NULL",
    "ALTER TABLE clients ALTER COLUMN agent_id SET NOT NULL",
    "ALTER TABLE client_invoices ALTER COLUMN agent_id SET NOT NULL",
)

# Matches alembic revision f2d9a6c4b1e3 (service catalogue). The services table
# itself is created by create_all() before these run.
SERVICES = (
    """
    ALTER TABLE client_invoices
        ADD COLUMN IF NOT EXISTS service_id INTEGER REFERENCES services(id),
        ADD COLUMN IF NOT EXISTS service_title VARCHAR(200)
    """,
)

# Matches alembic revision a7c3e9d2f4b6 (payments received by bank).
PAYMENTS_BANK = (
    "ALTER TYPE paymentmode ADD VALUE IF NOT EXISTS 'BANK'",
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS bank_name VARCHAR(120)",
)

# Matches alembic revision c5f1a8e3d7b9 (vendor onboarding). The
# vendor_bank_accounts table itself is created by create_all() before these run.
VENDOR_ONBOARDING = (
    """
    ALTER TABLE vendors
        ADD COLUMN IF NOT EXISTS msme_number VARCHAR(30),
        ADD COLUMN IF NOT EXISTS coi VARCHAR(120)
    """,
    # Carry each vendor's single legacy bank account into the new table, once.
    """
    INSERT INTO vendor_bank_accounts (vendor_id, account_holder, bank_name, account_number, ifsc_code, created_at, updated_at)
    SELECT v.id, COALESCE(v.bank_account_holder, v.business_name), COALESCE(v.bank_name, ''),
           v.account_number, COALESCE(v.ifsc_code, ''), now(), now()
    FROM vendors v
    WHERE v.account_number IS NOT NULL AND v.account_number <> ''
      AND NOT EXISTS (SELECT 1 FROM vendor_bank_accounts b WHERE b.vendor_id = v.id)
    """,
)

# Matches alembic revision d8a2f6c1e4b7 (projects). The projects and
# project_vendors tables are created by create_all() before these run.
PROJECTS = (
    "ALTER TABLE vendor_invoices ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES projects(id)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_invoices_project_id ON vendor_invoices (project_id)",
    # Each old per-vendor allocation becomes a project with that one vendor, once.
    """
    INSERT INTO projects (code, title, description, client_id, budget, start_date, end_date,
                          expected_report_date, status, created_at, updated_at)
    SELECT 'ALLOC-' || a.id, a.project_name, a.scope_of_work, a.client_id, a.agreed_cost, a.start_date,
           a.end_date, a.expected_report_date, a.status, now(), now()
    FROM vendor_allocations a
    WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE lower(p.code) = lower('ALLOC-' || a.id))
    """,
    """
    INSERT INTO project_vendors (project_id, vendor_id, work_percent, agreed_cost, work_order_outdated,
                                 created_at, updated_at)
    SELECT p.id, a.vendor_id, a.allocation_percent, a.agreed_cost, false, now(), now()
    FROM vendor_allocations a
    JOIN projects p ON p.code = 'ALLOC-' || a.id
    WHERE NOT EXISTS (SELECT 1 FROM project_vendors pv WHERE pv.project_id = p.id)
    """,
    """
    UPDATE vendor_invoices vi SET project_id = p.id
    FROM projects p
    WHERE vi.project_id IS NULL AND vi.allocation_id IS NOT NULL AND p.code = 'ALLOC-' || vi.allocation_id
    """,
)

# Matches alembic revision e3b7c9a2d5f1 (GST and TDS tracking).
TAX_TRACKING = (
    """
    ALTER TABLE payments
        ADD COLUMN IF NOT EXISTS tds_certificate_number VARCHAR(60),
        ADD COLUMN IF NOT EXISTS tds_certificate_date DATE
    """,
    """
    ALTER TABLE vendor_invoices
        ADD COLUMN IF NOT EXISTS gst_rate DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS is_interstate BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS cgst DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS sgst DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS igst DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS vendor_gstin VARCHAR(30),
        ADD COLUMN IF NOT EXISTS tds_section VARCHAR(30),
        ADD COLUMN IF NOT EXISTS tds_deposited_on DATE,
        ADD COLUMN IF NOT EXISTS tds_challan_number VARCHAR(60)
    """,
    # Invoices paid "total minus TDS" before TDS counted towards settlement were left
    # Partially Paid with the TDS shown as pending. Settle them on cash plus TDS.
    """
    UPDATE client_invoices i
    SET amount_pending = GREATEST(ROUND((i.total_amount - s.settled)::numeric, 2), 0),
        status = CASE WHEN s.settled >= i.total_amount - 0.01 THEN 'FULLY_PAID'::invoicestatus ELSE i.status END
    FROM (
        SELECT invoice_id, SUM(amount + COALESCE(tds_deducted, 0)) AS settled
        FROM payments GROUP BY invoice_id
    ) s
    WHERE s.invoice_id = i.id AND i.status = 'PARTIALLY_PAID'
    """,
)


def _run(engine: Engine, statements: tuple[str, ...]) -> None:
    # AUTOCOMMIT: ALTER TYPE ... ADD VALUE must not share a transaction with later statements.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001 - log and keep starting; the API reports real failures
                logger.error("Schema upgrade failed: %s (%s)", stmt.strip().splitlines()[0], exc)


def apply(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    _run(engine, INVOICE_WORKFLOW + AGENTS_COLUMNS + SERVICES + PAYMENTS_BANK + VENDOR_ONBOARDING + PROJECTS + TAX_TRACKING)
    _ensure_house_agent(engine)
    _run(engine, AGENTS_BACKFILL)


def _ensure_house_agent(engine: Engine) -> None:
    from app.services.agents import ensure_house_agent  # local import: services import models

    with Session(engine) as db:
        ensure_house_agent(db)
        db.commit()
