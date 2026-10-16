"""project invoicing: projects carry a service and contract value; invoices belong to a project

Revision ID: f6c2b8d4a1e3
Revises: e3b7c9a2d5f1
Create Date: 2026-10-15 12:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import PROJECT_INVOICING

revision: str = 'f6c2b8d4a1e3'
down_revision: Union[str, None] = 'e3b7c9a2d5f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in PROJECT_INVOICING:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE client_invoices DROP COLUMN IF EXISTS project_id")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS contract_value, DROP COLUMN IF EXISTS service_id")
