"""data upgrades: marks one-time data steps such as the standard service list

Revision ID: c9f5a2b7d4e1
Revises: b8e4f1a6c3d9
Create Date: 2026-10-16 13:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import DATA_UPGRADES

revision: str = 'c9f5a2b7d4e1'
down_revision: Union[str, None] = 'b8e4f1a6c3d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in DATA_UPGRADES:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_upgrades")
