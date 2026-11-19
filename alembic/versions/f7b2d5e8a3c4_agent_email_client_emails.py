"""agent email optional; clients can have more email addresses

Revision ID: f7b2d5e8a3c4
Revises: b7e4d1a9c3f6
Create Date: 2026-11-19 12:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import AGENT_EMAIL_AND_CLIENT_EMAILS

revision: str = 'f7b2d5e8a3c4'
down_revision: Union[str, None] = 'b7e4d1a9c3f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in AGENT_EMAIL_AND_CLIENT_EMAILS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS other_emails")
    # Agents without an email cannot be made NOT NULL again without data; left nullable.
