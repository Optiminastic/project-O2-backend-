"""Ask O2 assistant tables.

Revision ID: e1a7c3f9b2d4
Revises: d9f3b7a2e5c1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1a7c3f9b2d4"
down_revision: Union[str, None] = "d9f3b7a2e5c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONVERSATIONS = "assistant_conversations.id"


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey(CONVERSATIONS, ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "assistant_actions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey(CONVERSATIONS, ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("message_id", sa.Integer, sa.ForeignKey("assistant_messages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tool", sa.String(80), nullable=False),
        sa.Column("tool_call_id", sa.String(120), nullable=False),
        sa.Column("arguments", sa.JSON, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("fields", sa.JSON, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "assistant_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey(CONVERSATIONS, ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in ("assistant_files", "assistant_actions", "assistant_messages", "assistant_conversations"):
        op.drop_table(table)
