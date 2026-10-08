"""invoice approval workflow: proformas, client links, gapless numbering

Revision ID: d4a7c2e9f1b8
Revises: c3e8f1a2b4d6
Create Date: 2026-10-06 15:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import INVOICE_WORKFLOW

revision: str = 'd4a7c2e9f1b8'
down_revision: Union[str, None] = 'c3e8f1a2b4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = (
    """
    DO $$ BEGIN
        CREATE TYPE invoicelinkpurpose AS ENUM ('APPROVE', 'VIEW');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$
    """,
    """
    CREATE TABLE IF NOT EXISTS invoice_links (
        id SERIAL PRIMARY KEY,
        invoice_id INTEGER NOT NULL REFERENCES client_invoices(id),
        token VARCHAR(64) NOT NULL,
        purpose invoicelinkpurpose NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        used_at TIMESTAMPTZ,
        revoked_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_invoice_links_token ON invoice_links (token)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_links_invoice_id ON invoice_links (invoice_id)",
    """
    CREATE TABLE IF NOT EXISTS document_sequences (
        series VARCHAR(40) PRIMARY KEY,
        last_value INTEGER NOT NULL
    )
    """,
)


def upgrade() -> None:
    for stmt in INVOICE_WORKFLOW:
        op.execute(stmt)
    for stmt in NEW_TABLES:
        op.execute(stmt)


def downgrade() -> None:
    # Enum values cannot be removed from invoicestatus in Postgres; they stay.
    op.execute("DROP TABLE IF EXISTS invoice_links")
    op.execute("DROP TYPE IF EXISTS invoicelinkpurpose")
    op.execute("DROP TABLE IF EXISTS document_sequences")
    op.execute("DROP INDEX IF EXISTS ix_client_invoices_proforma_number")
    op.execute(
        """
        ALTER TABLE client_invoices
            DROP COLUMN IF EXISTS proforma_number,
            DROP COLUMN IF EXISTS proforma_date,
            DROP COLUMN IF EXISTS sac_code,
            DROP COLUMN IF EXISTS created_by_id,
            DROP COLUMN IF EXISTS client_approved_at,
            DROP COLUMN IF EXISTS client_approval_method,
            DROP COLUMN IF EXISTS client_approver_name,
            DROP COLUMN IF EXISTS client_approval_note,
            DROP COLUMN IF EXISTS change_request_note,
            DROP COLUMN IF EXISTS change_requested_by,
            DROP COLUMN IF EXISTS issued_by_id,
            DROP COLUMN IF EXISTS issued_at
        """
    )
    op.execute("DROP TYPE IF EXISTS clientapprovalmethod")
