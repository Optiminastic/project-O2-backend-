"""Optiminastic commission on vendor bills, bill documents, payouts split over bills and service lines

Revision ID: d9f3b7a2e5c1
Revises: c8e2a6f4d1b7
Create Date: 2026-11-16 18:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import VENDOR_PAYOUTS

revision: str = 'd9f3b7a2e5c1'
down_revision: Union[str, None] = 'c8e2a6f4d1b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in VENDOR_PAYOUTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payout_allocations")
    op.execute("ALTER TABLE payment_approvals DROP COLUMN IF EXISTS vendor_id")
    op.execute(
        "ALTER TABLE vendor_invoices DROP COLUMN IF EXISTS commission_rate, DROP COLUMN IF EXISTS commission_amount, "
        "DROP COLUMN IF EXISTS amount_payable, DROP COLUMN IF EXISTS document_path, DROP COLUMN IF EXISTS document_name"
    )
    op.execute("ALTER TABLE vendors DROP COLUMN IF EXISTS commission_rate")
