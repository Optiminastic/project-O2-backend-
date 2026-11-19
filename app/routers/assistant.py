"""Ask O2: the CEO's and CFO's assistant. Every change it proposes runs only after they confirm it."""

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.assistant import llm, runner
from app.config import settings
from app.core.deps import require_roles
from app.database import get_db
from app.models import AssistantConversation, AssistantFile, User, UserRole
from app.services import storage

router = APIRouter(prefix="/assistant", tags=["assistant"])
allowed = require_roles(UserRole.ADMIN_CEO, UserRole.CFO)

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_CHATS_LISTED = 50
FILE_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
NOT_SET_UP = "Ask O2 is not set up yet. Add OPENROUTER_API_KEY to the API's environment."
UNAVAILABLE = "The assistant could not reply. Try again."


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=runner.MAX_TEXT)
    file_ids: list[int] = []


def _conversation(db: Session, user: User, conversation_id: int) -> AssistantConversation:
    row = db.get(AssistantConversation, conversation_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such chat.")
    return row


@router.get("/status")
def status(user: User = Depends(allowed)):
    return {"configured": llm.is_configured(), "model": settings.openrouter_model}


@router.get("/conversations")
def list_conversations(db: Session = Depends(get_db), user: User = Depends(allowed)):
    rows = (
        db.query(AssistantConversation)
        .filter_by(user_id=user.id)
        .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id.desc())
        .limit(MAX_CHATS_LISTED)
        .all()
    )
    return [{"id": r.id, "title": r.title, "updated_at": r.updated_at} for r in rows]


@router.post("/conversations")
def new_conversation(db: Session = Depends(get_db), user: User = Depends(allowed)):
    row = AssistantConversation(user_id=user.id, title=runner.NEW_CHAT)
    db.add(row)
    db.commit()
    return runner.conversation_view(db, row)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db), user: User = Depends(allowed)):
    return runner.conversation_view(db, _conversation(db, user, conversation_id))


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, db: Session = Depends(get_db), user: User = Depends(allowed)):
    db.delete(_conversation(db, user, conversation_id))
    db.commit()
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: int, payload: MessageIn, db: Session = Depends(get_db), user: User = Depends(allowed)
):
    conversation = _conversation(db, user, conversation_id)
    if not llm.is_configured():
        raise HTTPException(status_code=503, detail=NOT_SET_UP)
    try:
        runner.handle_message(db, user, conversation, payload.text, payload.file_ids)
    except llm.AssistantUnavailable:
        raise HTTPException(status_code=502, detail=UNAVAILABLE)
    return runner.conversation_view(db, conversation)


@router.post("/conversations/{conversation_id}/files")
async def attach_file(
    conversation_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(allowed),
):
    conversation = _conversation(db, user, conversation_id)
    if file.content_type not in FILE_TYPES:
        raise HTTPException(status_code=422, detail="Attach a PDF, an image, a CSV or an Excel file.")
    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=422, detail="Files can be up to 10 MB.")
    name = file.filename or "file"
    row = AssistantFile(
        conversation_id=conversation.id, user_id=user.id, file_name=name, content_type=file.content_type,
        size=len(content), file_path=storage.save(content, name, file.content_type),
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.file_name}


@router.post("/actions/{action_id}/confirm")
def confirm(action_id: int, db: Session = Depends(get_db), user: User = Depends(allowed)):
    return runner.conversation_view(db, runner.confirm_action(db, user, action_id))


@router.post("/actions/{action_id}/cancel")
def cancel(action_id: int, db: Session = Depends(get_db), user: User = Depends(allowed)):
    return runner.conversation_view(db, runner.cancel_action(db, user, action_id))
