"""Ask O2: saved chats, proposed changes, attached files, connected plugins and saved commands."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, utcnow

ACTION_PENDING = "pending"
ACTION_RUNNING = "running"
ACTION_DONE = "done"
ACTION_FAILED = "failed"
ACTION_CANCELLED = "cancelled"
ACTION_EXPIRED = "expired"


class AssistantConversation(TimestampMixin, Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="New chat")
    messages: Mapped[list["AssistantMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="AssistantMessage.id"
    )


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True
    )
    # "user", "assistant" or "tool", as the chat API names them.
    role: Mapped[str] = mapped_column(String(20))
    # The chat API message itself (text, tool calls or a tool result), plus file ids on user messages.
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    conversation: Mapped[AssistantConversation] = relationship(back_populates="messages")


class AssistantAction(Base):
    """A tool call shown to the user: a change waiting for their Yes, or a download."""

    __tablename__ = "assistant_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("assistant_messages.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    tool_call_id: Mapped[str] = mapped_column(String(120))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(20), default="write")  # "write" or "download"
    status: Mapped[str] = mapped_column(String(20), default=ACTION_PENDING, index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssistantFile(Base):
    __tablename__ = "assistant_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    file_path: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssistantPlugin(TimestampMixin, Base):
    """A remote MCP server the CEO connected. Its tools join Ask O2's own for the CEO and CFO.

    ``connection`` holds the URL and auth headers, encrypted (see app.assistant.secrets): a URL can carry a
    key, so neither is ever stored or returned in the clear. ``host`` is the safe part to show.
    """

    __tablename__ = "assistant_plugins"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    host: Mapped[str] = mapped_column(String(255))
    directory_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    connection: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # What the server offered at the last connect: [{name, description, input_schema, read_only}].
    tools: Mapped[list] = mapped_column(JSON, default=list)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AssistantCommand(TimestampMixin, Base):
    """A saved "/" command: typing /name in Ask O2 drops its prompt into the message box."""

    __tablename__ = "assistant_commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    description: Mapped[str] = mapped_column(String(160), default="")
    prompt: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
