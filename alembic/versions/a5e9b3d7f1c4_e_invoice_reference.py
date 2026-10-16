"""e-invoice: the IRN on a tax invoice; until one is recorded the e-invoice shows as pending

Revision ID: a5e9b3d7f1c4
Revises: f4d8a2c6e9b3
Create Date: 2026-10-16 20:30:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import E_INVOICE

revision: str = 'a5e9b3d7f1c4'
down_revision: Union[str, None] = 'f4d8a2c6e9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in E_INVOICE:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE client_invoices DROP COLUMN IF EXISTS irn")
