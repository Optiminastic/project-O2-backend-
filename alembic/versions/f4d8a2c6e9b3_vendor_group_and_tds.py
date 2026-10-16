"""vendors: group company flag (CFO/CEO only) and the default TDS section for their bills

Revision ID: f4d8a2c6e9b3
Revises: e3c7f9b5d2a8
Create Date: 2026-10-16 20:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import VENDOR_GROUP_AND_TDS

revision: str = 'f4d8a2c6e9b3'
down_revision: Union[str, None] = 'e3c7f9b5d2a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in VENDOR_GROUP_AND_TDS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE vendors DROP COLUMN IF EXISTS default_tds_section")
    op.execute("ALTER TABLE vendors DROP COLUMN IF EXISTS is_group_company")
