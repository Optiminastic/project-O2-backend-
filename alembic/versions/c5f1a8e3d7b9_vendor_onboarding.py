"""vendor onboarding: MSME and COI fields, several bank accounts per vendor

Revision ID: c5f1a8e3d7b9
Revises: a7c3e9d2f4b6
Create Date: 2026-10-08 12:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import VENDOR_ONBOARDING

revision: str = 'c5f1a8e3d7b9'
down_revision: Union[str, None] = 'a7c3e9d2f4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_bank_accounts (
            id SERIAL PRIMARY KEY,
            vendor_id INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
            account_holder VARCHAR(160) NOT NULL,
            bank_name VARCHAR(160) NOT NULL,
            account_number VARCHAR(40) NOT NULL,
            ifsc_code VARCHAR(20) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_vendor_bank_accounts_vendor_id ON vendor_bank_accounts (vendor_id)")
    for stmt in VENDOR_ONBOARDING:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vendor_bank_accounts")
    op.execute("ALTER TABLE vendors DROP COLUMN IF EXISTS coi, DROP COLUMN IF EXISTS msme_number")
