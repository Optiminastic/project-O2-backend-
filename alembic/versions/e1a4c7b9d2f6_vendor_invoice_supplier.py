"""supplier details as printed on a vendor bill: name, address, COI

Revision ID: e1a4c7b9d2f6
Revises: d9f3b7a2e5c1
Create Date: 2026-11-16 20:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import VENDOR_INVOICE_SUPPLIER

revision: str = 'e1a4c7b9d2f6'
down_revision: Union[str, None] = 'd9f3b7a2e5c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in VENDOR_INVOICE_SUPPLIER:
        op.execute(stmt)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE vendor_invoices DROP COLUMN IF EXISTS supplier_name, "
        "DROP COLUMN IF EXISTS supplier_address, DROP COLUMN IF EXISTS supplier_coi"
    )
