"""multi-invoice receipts: payment rows of one bank receipt share a receipt_ref

Revision ID: e3c7f9b5d2a8
Revises: d2b6e8a4c1f7
Create Date: 2026-10-16 19:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import MULTI_INVOICE_RECEIPTS

revision: str = 'e3c7f9b5d2a8'
down_revision: Union[str, None] = 'd2b6e8a4c1f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in MULTI_INVOICE_RECEIPTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_payments_receipt_ref")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS receipt_ref")
