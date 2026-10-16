"""company banks: our bank accounts by name and last four digits; receipts point to one

Revision ID: b8e4f1a6c3d9
Revises: a7d3e9c5b2f4
Create Date: 2026-10-16 12:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import COMPANY_BANKS

revision: str = 'b8e4f1a6c3d9'
down_revision: Union[str, None] = 'a7d3e9c5b2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in COMPANY_BANKS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS bank_account_id")
    op.execute("DROP TABLE IF EXISTS company_banks")
