"""Chats as the panel shows them: what the user and the assistant said, and the action cards."""

from sqlalchemy.orm import Session

from app.models import AssistantAction, AssistantConversation, AssistantFile

# User messages carry their attachments as text for the model; the panel shows them as chips.
ATTACHMENT_MARKER = "\n[Attached file_id="


def _action(a: AssistantAction) -> dict:
    download = None
    if a.kind == "download" and a.result:
        download = {"path": a.result.get("path"), "filename": a.result.get("filename") or f"{a.tool}.pdf"}
    return {
        "id": a.id,
        "tool": a.tool,
        "kind": a.kind,
        "summary": a.summary,
        "fields": a.fields or [],
        "status": a.status,
        "error": a.error,
        "download": download,
        "created_at": a.created_at,
    }


def conversation_view(db: Session, conversation: AssistantConversation) -> dict:
    actions = (
        db.query(AssistantAction).filter_by(conversation_id=conversation.id).order_by(AssistantAction.id).all()
    )
    names = {f.id: f.file_name for f in db.query(AssistantFile).filter_by(conversation_id=conversation.id)}
    messages = []
    for m in conversation.messages:
        if m.role == "tool":
            continue
        cards = [_action(a) for a in actions if a.message_id == m.id]
        text = (m.content.get("content") or "").split(ATTACHMENT_MARKER)[0]
        if m.role == "assistant" and not text and not cards:
            continue
        messages.append({
            "id": m.id,
            "role": m.role,
            "text": text,
            "files": [{"id": i, "name": names.get(i, "file")} for i in m.content.get("file_ids", [])],
            "actions": cards,
        })
    return {
        "id": conversation.id,
        "title": conversation.title,
        "updated_at": conversation.updated_at,
        "messages": messages,
    }
