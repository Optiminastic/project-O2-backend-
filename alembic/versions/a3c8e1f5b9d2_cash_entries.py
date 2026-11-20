"""cash received and paid out to agents, against UPS codes

Revision ID: a3c8e1f5b9d2
Revises: f7b2d5e8a3c4
Create Date: 2026-11-20 10:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import CASH

revision: str = 'a3c8e1f5b9d2'
down_revision: Union[str, None] = 'f7b2d5e8a3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in CASH:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cash_entry_lines")
    op.execute("DROP TABLE IF EXISTS cash_entries")
