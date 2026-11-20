"""Idempotent schema upgrades applied at startup.

Production does not run Alembic on deploy, and ``create_all()`` only creates
missing tables, never new columns or enum values. Each statement here is safe
to run on every start. Keep in step with the matching Alembic revision.
"""

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import settings

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


# Matches alembic revision f6c2b8d4a1e3 (project-based invoicing).
PROJECT_INVOICING = (
    """
    ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS service_id INTEGER REFERENCES services(id),
        ADD COLUMN IF NOT EXISTS contract_value DOUBLE PRECISION NOT NULL DEFAULT 0
    """,
    "CREATE INDEX IF NOT EXISTS ix_projects_service_id ON projects (service_id)",
    "ALTER TABLE client_invoices ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES projects(id)",
    "CREATE INDEX IF NOT EXISTS ix_client_invoices_project_id ON client_invoices (project_id)",
)


# Matches alembic revision a7d3e9c5b2f4 (forms per the PROJECT O2 sheet).
FORMS_PER_SHEET = (
    """
    ALTER TABLE clients
        ADD COLUMN IF NOT EXISTS msme BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS msme_number VARCHAR(40)
    """,
    """
    CREATE TABLE IF NOT EXISTS project_services (
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        service_id INTEGER NOT NULL REFERENCES services(id),
        PRIMARY KEY (project_id, service_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_project_services_service_id ON project_services (service_id)",
    # Projects made when a project billed one service keep it as their only service.
    """
    INSERT INTO project_services (project_id, service_id)
    SELECT id, service_id FROM projects WHERE service_id IS NOT NULL
    ON CONFLICT DO NOTHING
    """,
    """
    CREATE TABLE IF NOT EXISTS invoice_lines (
        id SERIAL PRIMARY KEY,
        invoice_id INTEGER NOT NULL REFERENCES client_invoices(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        service_id INTEGER REFERENCES services(id),
        service_title VARCHAR(200) NOT NULL,
        description TEXT,
        sac_code VARCHAR(12),
        gst_rate DOUBLE PRECISION NOT NULL DEFAULT 18,
        taxable_value DOUBLE PRECISION NOT NULL DEFAULT 0,
        gst_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
        cgst DOUBLE PRECISION NOT NULL DEFAULT 0,
        sgst DOUBLE PRECISION NOT NULL DEFAULT 0,
        igst DOUBLE PRECISION NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_invoice_lines_invoice_id ON invoice_lines (invoice_id)",
)


# Matches alembic revision b8e4f1a6c3d9 (our bank accounts, named with their last four digits).
COMPANY_BANKS = (
    """
    CREATE TABLE IF NOT EXISTS company_banks (
        id SERIAL PRIMARY KEY,
        name VARCHAR(160) NOT NULL,
        account_last4 VARCHAR(4) NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_company_banks_name_last4 ON company_banks (lower(name), account_last4)",
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS bank_account_id INTEGER REFERENCES company_banks(id)",
    "CREATE INDEX IF NOT EXISTS ix_payments_bank_account_id ON payments (bank_account_id)",
)


# Matches alembic revision c9f5a2b7d4e1 (one-time data steps, e.g. the standard service list).
DATA_UPGRADES = (
    """
    CREATE TABLE IF NOT EXISTS data_upgrades (
        name VARCHAR(80) PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)


# Matches alembic revision d2b6e8a4c1f7 (coordinator role).
COORDINATOR_ROLE = ("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'COORDINATOR'",)


# Matches alembic revision e3c7f9b5d2a8 (one receipt across several invoices).
MULTI_INVOICE_RECEIPTS = (
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS receipt_ref VARCHAR(36)",
    "CREATE INDEX IF NOT EXISTS ix_payments_receipt_ref ON payments (receipt_ref)",
)


# Matches alembic revision f4d8a2c6e9b3 (group company vendors, default TDS section).
VENDOR_GROUP_AND_TDS = (
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS is_group_company BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS default_tds_section VARCHAR(30)",
)


# Matches alembic revision a5e9b3d7f1c4 (e-invoice reference on tax invoices).
E_INVOICE = ("ALTER TABLE client_invoices ADD COLUMN IF NOT EXISTS irn VARCHAR(64)",)

# Vendor form per the sheet: MSME is a Yes/No, its Udyam number optional. A vendor
# that already has a Udyam number is an MSME.
VENDOR_MSME = (
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS msme BOOLEAN NOT NULL DEFAULT false",
    "UPDATE vendors SET msme = true WHERE msme = false AND COALESCE(msme_number, '') <> ''",
)


# Matches alembic revision c8e2a6f4d1b7 (vendors assigned to projects per service).
# Each vendor already on a project becomes one assigned service: the project's first
# service at the vendor's full agreed cost, coded <project code>-V<nn>-S01. Old bills
# for that vendor on that project are charged against it.
PROJECT_VENDOR_SERVICES = (
    """
    CREATE TABLE IF NOT EXISTS vendor_services (
        vendor_id INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
        service_id INTEGER NOT NULL REFERENCES services(id),
        PRIMARY KEY (vendor_id, service_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_vendor_services_service_id ON vendor_services (service_id)",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS last_vendor_seq INTEGER NOT NULL DEFAULT 0",
    """
    ALTER TABLE project_vendors
        ADD COLUMN IF NOT EXISTS vendor_seq INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS last_service_seq INTEGER NOT NULL DEFAULT 0
    """,
    """
    CREATE TABLE IF NOT EXISTS project_vendor_services (
        id SERIAL PRIMARY KEY,
        project_vendor_id INTEGER NOT NULL REFERENCES project_vendors(id) ON DELETE CASCADE,
        service_id INTEGER NOT NULL REFERENCES services(id),
        code VARCHAR(60) NOT NULL UNIQUE,
        agreed_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_project_vendor_services_service UNIQUE (project_vendor_id, service_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_project_vendor_services_project_vendor_id ON project_vendor_services (project_vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_project_vendor_services_service_id ON project_vendor_services (service_id)",
    """
    CREATE TABLE IF NOT EXISTS vendor_invoice_lines (
        id SERIAL PRIMARY KEY,
        vendor_invoice_id INTEGER NOT NULL REFERENCES vendor_invoices(id) ON DELETE CASCADE,
        assignment_id INTEGER NOT NULL REFERENCES project_vendor_services(id),
        amount DOUBLE PRECISION NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_vendor_invoice_lines_vendor_invoice_id ON vendor_invoice_lines (vendor_invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_invoice_lines_assignment_id ON vendor_invoice_lines (assignment_id)",
    # Number the vendors already on each project V01, V02... in the order they were added.
    """
    UPDATE project_vendors pv SET vendor_seq = n.seq
    FROM (
        SELECT id, (SELECT COALESCE(MAX(vendor_seq), 0) FROM project_vendors x WHERE x.project_id = p.project_id)
                   + row_number() OVER (PARTITION BY project_id ORDER BY id) AS seq
        FROM project_vendors p WHERE vendor_seq = 0
    ) n
    WHERE pv.id = n.id
    """,
    """
    UPDATE projects p SET last_vendor_seq = s.top
    FROM (SELECT project_id, MAX(vendor_seq) AS top FROM project_vendors GROUP BY project_id) s
    WHERE s.project_id = p.id AND p.last_vendor_seq < s.top
    """,
    # One assigned service per existing vendor on a project with a service.
    """
    INSERT INTO project_vendor_services (project_vendor_id, service_id, code, agreed_cost, created_at, updated_at)
    SELECT pv.id, p.service_id, p.code || '-V' || lpad(pv.vendor_seq::text, 2, '0') || '-S01', pv.agreed_cost,
           now(), now()
    FROM project_vendors pv JOIN projects p ON p.id = pv.project_id
    WHERE p.service_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM project_vendor_services s WHERE s.project_vendor_id = pv.id)
    """,
    """
    UPDATE project_vendors pv SET last_service_seq = 1
    WHERE pv.last_service_seq = 0 AND EXISTS (SELECT 1 FROM project_vendor_services s WHERE s.project_vendor_id = pv.id)
    """,
    # The vendor provides the services it is assigned.
    """
    INSERT INTO vendor_services (vendor_id, service_id)
    SELECT DISTINCT pv.vendor_id, s.service_id
    FROM project_vendor_services s JOIN project_vendors pv ON pv.id = s.project_vendor_id
    ON CONFLICT DO NOTHING
    """,
    # An old bill on a project charges the vendor's service there, when it has exactly one.
    """
    INSERT INTO vendor_invoice_lines (vendor_invoice_id, assignment_id, amount)
    SELECT vi.id, MIN(s.id), vi.invoice_amount
    FROM vendor_invoices vi
    JOIN project_vendors pv ON pv.project_id = vi.project_id AND pv.vendor_id = vi.vendor_id
    JOIN project_vendor_services s ON s.project_vendor_id = pv.id
    WHERE NOT EXISTS (SELECT 1 FROM vendor_invoice_lines l WHERE l.vendor_invoice_id = vi.id)
    GROUP BY vi.id, vi.invoice_amount
    HAVING COUNT(s.id) = 1
    """,
)


# Matches alembic revision d9f3b7a2e5c1 (Optiminastic commission, vendor invoice
# documents, vendor payouts split over bills and their service lines).
VENDOR_PAYOUTS = (
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS commission_rate DOUBLE PRECISION NOT NULL DEFAULT 0",
    """
    ALTER TABLE vendor_invoices
        ADD COLUMN IF NOT EXISTS commission_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS commission_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS amount_payable DOUBLE PRECISION NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS document_path VARCHAR(500),
        ADD COLUMN IF NOT EXISTS document_name VARCHAR(200)
    """,
    # Bills from before commissions: the vendor is paid the whole net payable.
    "UPDATE vendor_invoices SET amount_payable = net_payable - commission_amount WHERE amount_payable = 0 AND net_payable > 0",
    "ALTER TABLE payment_approvals ADD COLUMN IF NOT EXISTS vendor_id INTEGER REFERENCES vendors(id)",
    "CREATE INDEX IF NOT EXISTS ix_payment_approvals_vendor_id ON payment_approvals (vendor_id)",
    """
    CREATE TABLE IF NOT EXISTS payout_allocations (
        id SERIAL PRIMARY KEY,
        approval_id INTEGER NOT NULL REFERENCES payment_approvals(id) ON DELETE CASCADE,
        vendor_invoice_id INTEGER NOT NULL REFERENCES vendor_invoices(id),
        line_id INTEGER REFERENCES vendor_invoice_lines(id),
        amount DOUBLE PRECISION NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_payout_allocations_approval_id ON payout_allocations (approval_id)",
    "CREATE INDEX IF NOT EXISTS ix_payout_allocations_vendor_invoice_id ON payout_allocations (vendor_invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_payout_allocations_line_id ON payout_allocations (line_id)",
    # A payout raised against one bill before allocations settles that bill.
    """
    INSERT INTO payout_allocations (approval_id, vendor_invoice_id, line_id, amount)
    SELECT a.id, a.vendor_invoice_id, NULL, a.net_payable
    FROM payment_approvals a
    WHERE a.vendor_invoice_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM payout_allocations x WHERE x.approval_id = a.id)
    """,
    """
    UPDATE payment_approvals a SET vendor_id = vi.vendor_id
    FROM vendor_invoices vi
    WHERE a.vendor_invoice_id = vi.id AND a.vendor_id IS NULL
    """,
)


# Matches alembic revision e1a4c7b9d2f6 (supplier details as printed on a vendor bill).
VENDOR_INVOICE_SUPPLIER = (
    """
    ALTER TABLE vendor_invoices
        ADD COLUMN IF NOT EXISTS supplier_name VARCHAR(200),
        ADD COLUMN IF NOT EXISTS supplier_address TEXT,
        ADD COLUMN IF NOT EXISTS supplier_coi VARCHAR(120)
    """,
)


# Matches alembic revision f7b2d5e8a3c4 (agent email optional, client's other emails).
AGENT_EMAIL_AND_CLIENT_EMAILS = (
    "ALTER TABLE agents ALTER COLUMN email DROP NOT NULL",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS other_emails TEXT",
)


def _run(engine: Engine, statements: tuple[str, ...]) -> None:
    # AUTOCOMMIT: ALTER TYPE ... ADD VALUE must not share a transaction with later statements.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001 - log and keep starting; the API reports real failures
                logger.error("Schema upgrade failed: %s (%s)", stmt.strip().splitlines()[0], exc)


# Matches alembic revision e1a7c3f9b2d4 (Ask O2 assistant). The tables come from create_all;
# this keeps the pending-action lookup fast on databases created before the model had the index.
# Matches alembic revision a3c8e1f5b9d2 (cash received / paid out to agents, against UPS codes).
CASH = (
    """
    CREATE TABLE IF NOT EXISTS cash_entries (
        id SERIAL PRIMARY KEY,
        direction VARCHAR(3) NOT NULL,
        entry_date DATE NOT NULL,
        amount DOUBLE PRECISION NOT NULL DEFAULT 0,
        agent_id INTEGER REFERENCES agents(id),
        note TEXT,
        created_by_id INTEGER REFERENCES users(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_cash_entries_direction ON cash_entries (direction)",
    "CREATE INDEX IF NOT EXISTS ix_cash_entries_entry_date ON cash_entries (entry_date)",
    "CREATE INDEX IF NOT EXISTS ix_cash_entries_agent_id ON cash_entries (agent_id)",
    """
    CREATE TABLE IF NOT EXISTS cash_entry_lines (
        id SERIAL PRIMARY KEY,
        entry_id INTEGER NOT NULL REFERENCES cash_entries(id) ON DELETE CASCADE,
        assignment_id INTEGER NOT NULL REFERENCES project_vendor_services(id),
        amount DOUBLE PRECISION NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_cash_entry_lines_entry_id ON cash_entry_lines (entry_id)",
    "CREATE INDEX IF NOT EXISTS ix_cash_entry_lines_assignment_id ON cash_entry_lines (assignment_id)",
)


ASSISTANT = ("CREATE INDEX IF NOT EXISTS ix_assistant_actions_status ON assistant_actions (status)",)


def apply(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    _run(
        engine,
        INVOICE_WORKFLOW + AGENTS_COLUMNS + SERVICES + PAYMENTS_BANK + VENDOR_ONBOARDING + PROJECTS + TAX_TRACKING
        + PROJECT_INVOICING + FORMS_PER_SHEET + COMPANY_BANKS + DATA_UPGRADES + COORDINATOR_ROLE + MULTI_INVOICE_RECEIPTS
        + VENDOR_GROUP_AND_TDS + E_INVOICE + VENDOR_MSME + PROJECT_VENDOR_SERVICES + VENDOR_PAYOUTS
        + VENDOR_INVOICE_SUPPLIER + AGENT_EMAIL_AND_CLIENT_EMAILS + ASSISTANT + CASH,
    )
    _ensure_house_agent(engine)
    if settings.seed_service_catalogue:
        _seed_service_catalogue(engine)
    _run(engine, AGENTS_BACKFILL)


def _seed_service_catalogue(engine: Engine) -> None:
    from app.services.catalogue_seed import seed_standard_services  # local import: services import models

    try:
        with Session(engine) as db:
            seed_standard_services(db)
            db.commit()
    except Exception as exc:  # noqa: BLE001 - never block startup over the catalogue
        logger.error("Adding the standard service list failed: %s", exc)


def _ensure_house_agent(engine: Engine) -> None:
    from app.services.agents import ensure_house_agent  # local import: services import models

    with Session(engine) as db:
        ensure_house_agent(db)
        db.commit()
