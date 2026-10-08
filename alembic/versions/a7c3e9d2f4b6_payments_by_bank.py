"""payments received by bank: BANK payment mode and bank_name

Revision ID: a7c3e9d2f4b6
Revises: f2d9a6c4b1e3
Create Date: 2026-10-07 15:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import PAYMENTS_BANK

revision: str = 'a7c3e9d2f4b6'
down_revision: Union[str, None] = 'f2d9a6c4b1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE cannot run inside the migration transaction on older Postgres.
    with op.get_context().autocommit_block():
        for stmt in PAYMENTS_BANK:
            op.execute(stmt)


def downgrade() -> None:
    # Postgres cannot drop an enum value; BANK stays in paymentmode.
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS bank_name")
