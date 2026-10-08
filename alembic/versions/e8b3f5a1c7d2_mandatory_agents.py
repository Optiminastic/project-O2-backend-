"""mandatory agents: in-house "Opti" agent and NOT NULL agent_id

Revision ID: e8b3f5a1c7d2
Revises: d4a7c2e9f1b8
Create Date: 2026-10-07 10:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import settings
from app.schema_upgrades import AGENTS_BACKFILL, AGENTS_COLUMNS
from app.services.agents import HOUSE_AGENT_LEGAL_NAME, HOUSE_AGENT_NAME

revision: str = 'e8b3f5a1c7d2'
down_revision: Union[str, None] = 'd4a7c2e9f1b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in AGENTS_COLUMNS:
        op.execute(stmt)
    bind = op.get_bind()
    # After a downgrade the old house row survives without its flag: adopt it rather than duplicate it.
    bind.execute(
        sa.text(
            """
            UPDATE agents SET is_house = true
            WHERE id = (SELECT min(id) FROM agents WHERE legal_name = :legal)
              AND NOT EXISTS (SELECT 1 FROM agents WHERE is_house)
            """
        ),
        {"legal": HOUSE_AGENT_LEGAL_NAME},
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO agents (business_name, legal_name, email, commission_rate, is_active, is_house, notes,
                                created_at, updated_at)
            SELECT :name, :legal, :email, 0, true, true,
                   'Default agent for clients who came in directly. Earns no commission.', now(), now()
            WHERE NOT EXISTS (SELECT 1 FROM agents WHERE is_house)
            """
        ),
        {"name": HOUSE_AGENT_NAME, "legal": HOUSE_AGENT_LEGAL_NAME, "email": f"finance@{settings.workspace_email_domain}"},
    )
    for stmt in AGENTS_BACKFILL:
        op.execute(stmt)


def downgrade() -> None:
    # Records keep the in-house agent; only the constraints and flag are removed.
    op.execute("ALTER TABLE client_invoices ALTER COLUMN agent_id DROP NOT NULL")
    op.execute("ALTER TABLE clients ALTER COLUMN agent_id DROP NOT NULL")
    op.execute("DROP INDEX IF EXISTS uq_agents_house")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS is_house")
