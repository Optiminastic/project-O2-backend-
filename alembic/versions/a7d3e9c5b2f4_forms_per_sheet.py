"""forms per the PROJECT O2 sheet: client MSME, several services per project, invoice lines

Revision ID: a7d3e9c5b2f4
Revises: f6c2b8d4a1e3
Create Date: 2026-10-16 10:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import FORMS_PER_SHEET

revision: str = 'a7d3e9c5b2f4'
down_revision: Union[str, None] = 'f6c2b8d4a1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in FORMS_PER_SHEET:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invoice_lines")
    op.execute("DROP TABLE IF EXISTS project_services")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS msme_number, DROP COLUMN IF EXISTS msme")
