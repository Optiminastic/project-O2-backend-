"""vendors provide services; vendors are assigned to projects per service, with a code

Revision ID: c8e2a6f4d1b7
Revises: b6f2d8a4e1c9
Create Date: 2026-11-16 12:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import PROJECT_VENDOR_SERVICES

revision: str = 'c8e2a6f4d1b7'
down_revision: Union[str, None] = 'b6f2d8a4e1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in PROJECT_VENDOR_SERVICES:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vendor_invoice_lines")
    op.execute("DROP TABLE IF EXISTS project_vendor_services")
    op.execute("DROP TABLE IF EXISTS vendor_services")
    op.execute("ALTER TABLE project_vendors DROP COLUMN IF EXISTS vendor_seq, DROP COLUMN IF EXISTS last_service_seq")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS last_vendor_seq")
