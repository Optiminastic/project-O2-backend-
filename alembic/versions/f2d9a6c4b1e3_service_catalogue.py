"""service catalogue: services table and service fields on client invoices

Revision ID: f2d9a6c4b1e3
Revises: e8b3f5a1c7d2
Create Date: 2026-10-07 12:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import SERVICES

revision: str = 'f2d9a6c4b1e3'
down_revision: Union[str, None] = 'e8b3f5a1c7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            sac_code VARCHAR(12),
            gst_rate DOUBLE PRECISION NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_services_title_lower ON services (lower(title))")
    for stmt in SERVICES:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE client_invoices DROP COLUMN IF EXISTS service_title, DROP COLUMN IF EXISTS service_id")
    op.execute("DROP TABLE IF EXISTS services")
