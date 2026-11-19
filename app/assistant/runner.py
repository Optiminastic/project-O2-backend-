"""One user message through the model and its tools, and confirming or cancelling a proposed change.

Reads run at once. A write is never run here: it becomes a pending action (a confirm card) and the
loop stops until the user decides. Confirming claims the action with one conditional update, so it
runs at most once, and only for the user who asked, within 30 minutes.
"""

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.assistant import llm, plugins
from app.assistant.describe import describe
from app.assistant.executor import ToolResult, for_model, run_tool
from app.assistant.formatting import plain_text
from app.assistant.tools import Tool, path_params, tool_by_name, tool_schemas
from app.assistant.views import ATTACHMENT_MARKER, conversation_view  # noqa: F401  (re-exported)
from app.models import (
    ACTION_CANCELLED,
    ACTION_DONE,
    ACTION_EXPIRED,
    ACTION_FAILED,
    ACTION_PENDING,
    ACTION_RUNNING,
    AssistantAction,
    AssistantConversation,
    AssistantFile,
    AssistantMessage,
    User,
)
from app.services.audit import log_action

PENDING_TTL = timedelta(minutes=30)
MAX_ROUNDS = 8
MAX_TEXT = 4000
TITLE_MAX = 60
NEW_CHAT = "New chat"

SYSTEM_PROMPT = """You are Ask O2, the assistant inside Project O2, Optiminastic's finance platform.
You help {name} ({role}). Today is {today}. Amounts are Indian rupees.
Use the tools to look things up and to make changes. Never invent ids: find them with a lookup first.
For any change, call the tool directly. The app shows the user a confirm card, so never ask "shall I?" in text.
Propose one change at a time and wait for its result before the next.
If a tool returns an error, explain it plainly and say what would fix it.
Attached files are listed in the user's message with their file_id. Keep replies short.
Tools named plugin<N>__... come from plugins the CEO connected (other services, not O2). Use them when the request is about that service.
Write plain text only: no Markdown, no ** or __ for bold, no # headings, no backticks. For a list, start each line with "- "."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _history(conversation: AssistantConversation, user: User) -> list[dict]:
    system = SYSTEM_PROMPT.format(name=user.name, role=user.role.value, today=date.today().isoformat())
    # The cache marker lets Claude reuse the tool list and this prompt across calls instead of
    # billing them in full each time. Other models ignore it.
    out = [{"role": "system", "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}]
    for m in conversation.messages:
        out.append({"role": m.role, **{k: v for k, v in m.content.items() if k != "file_ids"}})
    return out


def _add(db: Session, conversation: AssistantConversation, role: str, content: dict) -> AssistantMessage:
    msg = AssistantMessage(conversation_id=conversation.id, role=role, content=content)
    conversation.messages.append(msg)
    db.flush()
    return msg


def _tool_reply(db: Session, conversation: AssistantConversation, call_id: str, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    _add(db, conversation, "tool", {"tool_call_id": call_id, "content": text})


def _drop_pending(db: Session, conversation: AssistantConversation) -> None:
    """A new message moves on: a change still waiting is dropped, and the model is told so."""
    waiting = db.query(AssistantAction).filter_by(conversation_id=conversation.id, status=ACTION_PENDING).all()
    for action in waiting:
        action.status = ACTION_CANCELLED
        action.decided_at = _now()
        _tool_reply(db, conversation, action.tool_call_id, {"error": "The user did not confirm this change."})


def _parse(arguments: str) -> dict | None:
    try:
        value = json.loads(arguments or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _download(db: Session, conversation, msg, user: User, tool: Tool, call_id: str, args: dict) -> None:
    missing = path_params(tool) - args.keys()
    if missing:
        _tool_reply(db, conversation, call_id, {"error": f"Missing {', '.join(sorted(missing))}"})
        return
    path = tool.path.format(**{k: args[k] for k in path_params(tool)})
    db.add(AssistantAction(
        conversation_id=conversation.id, message_id=msg.id, user_id=user.id, tool=tool.name, tool_call_id=call_id,
        arguments=args, summary=tool.description, fields=describe(tool, args, db)["fields"], kind="download",
        status=ACTION_DONE, result={"path": path}, decided_at=_now(),
    ))
    _tool_reply(db, conversation, call_id, {"shown": "A download button is shown to the user."})


def _propose(db: Session, conversation, msg, user: User, tool, call_id: str, args: dict) -> None:
    """A confirm card for an O2 tool or a plugin tool."""
    if isinstance(tool, plugins.PluginTool):
        card, name = plugins.describe(tool, args), tool.public_name
    else:
        card, name = describe(tool, args, db), tool.name
    db.add(AssistantAction(
        conversation_id=conversation.id, message_id=msg.id, user_id=user.id, tool=name, tool_call_id=call_id,
        arguments=args, summary=card["summary"], fields=card["fields"], kind="write", status=ACTION_PENDING,
    ))


def _run_model(db: Session, user: User, conversation: AssistantConversation) -> None:
    from app.main import app  # noqa: PLC0415  (the router module imports this one)

    tools = tool_schemas(app.openapi()) + plugins.tool_schemas(db)
    for _ in range(MAX_ROUNDS):
        reply = llm.chat(_history(conversation, user), tools)
        # Some providers refuse an empty string next to tool calls; null is always accepted.
        content: dict = {"content": plain_text(reply.text) or None}
        if reply.tool_calls:
            content["tool_calls"] = [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in reply.tool_calls
            ]
        msg = _add(db, conversation, "assistant", content)
        if not reply.tool_calls:
            return
        proposed = False
        for c in reply.tool_calls:
            tool, args = tool_by_name(c["name"]) or plugins.find(db, c["name"]), _parse(c["arguments"])
            if tool is None or args is None:
                _tool_reply(db, conversation, c["id"], {"error": "Unknown tool or invalid arguments."})
            elif isinstance(tool, plugins.PluginTool) and tool.read_only:
                _tool_reply(db, conversation, c["id"], for_model(plugins.run(tool, args)))
            elif isinstance(tool, Tool) and tool.kind == "read":
                _tool_reply(db, conversation, c["id"], for_model(run_tool(tool, args, user, db)))
            elif isinstance(tool, Tool) and tool.kind == "download":
                _download(db, conversation, msg, user, tool, c["id"], args)
            elif proposed:
                _tool_reply(db, conversation, c["id"], {"error": "One change at a time. Propose this after the first is decided."})
            else:
                _propose(db, conversation, msg, user, tool, c["id"], args)
                proposed = True
        db.flush()
        if proposed:
            return
    _add(db, conversation, "assistant", {"content": "I stopped after too many lookups. Try a more specific request."})


def handle_message(db: Session, user: User, conversation: AssistantConversation, text: str, file_ids: list[int]) -> None:
    text = text.strip()
    if not text or len(text) > MAX_TEXT:
        raise HTTPException(status_code=422, detail=f"Write a message of 1 to {MAX_TEXT} characters.")
    files = (
        db.query(AssistantFile)
        .filter(AssistantFile.id.in_(file_ids or [0]), AssistantFile.conversation_id == conversation.id)
        .all()
    )
    _drop_pending(db, conversation)
    attached = "".join(f"{ATTACHMENT_MARKER}{f.id}: {f.file_name}]" for f in files)
    _add(db, conversation, "user", {"content": text + attached, "file_ids": [f.id for f in files]})
    if conversation.title == NEW_CHAT:
        conversation.title = text[:TITLE_MAX]
    db.commit()
    try:
        _run_model(db, user, conversation)
    finally:
        conversation.updated_at = _now()
        db.commit()


def _own_action(db: Session, user: User, action_id: int) -> AssistantAction:
    action = db.get(AssistantAction, action_id)
    if action is None or action.user_id != user.id or action.kind != "write":
        raise HTTPException(status_code=404, detail="No such change.")
    return action


def _follow_up(db: Session, user: User, action: AssistantAction, payload: str) -> AssistantConversation:
    """Tell the model how the change went and let it reply in a line."""
    conversation = db.get(AssistantConversation, action.conversation_id)
    _tool_reply(db, conversation, action.tool_call_id, payload)
    db.commit()
    try:
        _run_model(db, user, conversation)
    except (llm.AssistantNotConfigured, llm.AssistantUnavailable):
        pass  # the action card already shows what happened
    conversation.updated_at = _now()
    db.commit()
    return conversation


def confirm_action(db: Session, user: User, action_id: int) -> AssistantConversation:
    action = _own_action(db, user, action_id)
    if action.status == ACTION_PENDING and _aware(action.created_at) < _now() - PENDING_TTL:
        action.status = ACTION_EXPIRED
        action.decided_at = _now()
        db.commit()
        raise HTTPException(status_code=410, detail="This change expired. Ask again.")
    claimed = db.execute(
        update(AssistantAction)
        .where(AssistantAction.id == action.id, AssistantAction.status == ACTION_PENDING)
        .values(status=ACTION_RUNNING, decided_at=_now())
    ).rowcount
    db.commit()
    if not claimed:
        raise HTTPException(status_code=409, detail="This change was already decided.")
    db.refresh(action)
    result = _run_confirmed(db, user, action)
    action.status = ACTION_DONE if result.ok else ACTION_FAILED
    action.result = result.data if isinstance(result.data, dict) else {"value": result.data}
    action.error = result.error
    log_action(db, user, "Confirmed via Ask O2" if result.ok else "Failed via Ask O2", "assistant_action", action.id, action.summary)
    db.commit()
    return _follow_up(db, user, action, for_model(result))


def _run_confirmed(db: Session, user: User, action: AssistantAction) -> ToolResult:
    tool = tool_by_name(action.tool)
    if tool is not None:
        return run_tool(tool, action.arguments, user, db)
    plugin_tool = plugins.find(db, action.tool)
    if plugin_tool is None:
        return ToolResult(ok=False, status=410, data=None, error="That plugin was removed or turned off.")
    return plugins.run(plugin_tool, action.arguments)


def cancel_action(db: Session, user: User, action_id: int) -> AssistantConversation:
    action = _own_action(db, user, action_id)
    if action.status != ACTION_PENDING:
        raise HTTPException(status_code=409, detail="This change was already decided.")
    action.status = ACTION_CANCELLED
    action.decided_at = _now()
    db.commit()
    return _follow_up(db, user, action, json.dumps({"error": "The user cancelled this change."}))
