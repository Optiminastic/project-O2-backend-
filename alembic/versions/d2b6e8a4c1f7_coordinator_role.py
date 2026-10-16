"""coordinator role: approves proformas, which sends them to the client

Revision ID: d2b6e8a4c1f7
Revises: c9f5a2b7d4e1
Create Date: 2026-10-16 18:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import COORDINATOR_ROLE

revision: str = 'd2b6e8a4c1f7'
down_revision: Union[str, None] = 'c9f5a2b7d4e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot share a transaction with statements that use the value.
    with op.get_context().autocommit_block():
        for stmt in COORDINATOR_ROLE:
            op.execute(stmt)


def downgrade() -> None:
    # Postgres cannot drop an enum value; users with the role must be changed by hand first.
    pass
