"""vendor msme: a Yes/No answer, with the Udyam number optional

Revision ID: b6f2d8a4e1c9
Revises: a5e9b3d7f1c4
Create Date: 2026-11-06 12:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import VENDOR_MSME

revision: str = 'b6f2d8a4e1c9'
down_revision: Union[str, None] = 'a5e9b3d7f1c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in VENDOR_MSME:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE vendors DROP COLUMN IF EXISTS msme")
