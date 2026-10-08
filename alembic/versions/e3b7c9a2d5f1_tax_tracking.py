"""tax tracking: GST split and TDS sections on vendor bills, TDS deposits and client TDS certificates

Revision ID: e3b7c9a2d5f1
Revises: d8a2f6c1e4b7
Create Date: 2026-10-08 21:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import TAX_TRACKING

revision: str = 'e3b7c9a2d5f1'
down_revision: Union[str, None] = 'd8a2f6c1e4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in TAX_TRACKING:
        op.execute(stmt)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE payments DROP COLUMN IF EXISTS tds_certificate_date, DROP COLUMN IF EXISTS tds_certificate_number"
    )
    op.execute(
        """
        ALTER TABLE vendor_invoices
            DROP COLUMN IF EXISTS tds_challan_number, DROP COLUMN IF EXISTS tds_deposited_on,
            DROP COLUMN IF EXISTS tds_section, DROP COLUMN IF EXISTS vendor_gstin,
            DROP COLUMN IF EXISTS igst, DROP COLUMN IF EXISTS sgst, DROP COLUMN IF EXISTS cgst,
            DROP COLUMN IF EXISTS is_interstate, DROP COLUMN IF EXISTS gst_rate
        """
    )
