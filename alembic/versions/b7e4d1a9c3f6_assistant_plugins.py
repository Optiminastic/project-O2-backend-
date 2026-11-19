"""Ask O2 plugins (remote MCP servers) and saved commands.

Revision ID: b7e4d1a9c3f6
Revises: e1a7c3f9b2d4
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4d1a9c3f6"
down_revision: Union[str, None] = "e1a7c3f9b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "assistant_plugins",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("directory_key", sa.String(40), nullable=True),
        sa.Column("connection", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("tools", sa.JSON, nullable=False),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "assistant_commands",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("description", sa.String(160), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("assistant_commands")
    op.drop_table("assistant_plugins")
