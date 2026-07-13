import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.crud import chat as chat_crud
from app.crud import escalation as escalation_crud
from app.crud.user import get_active_tenant_id_by_slug
from app.models.user import UserDB
from app.schemas.chat import ChatMessageIn, ChatMessageOut, EscalatedQuestionOut, ResolveEscalationIn
from app.services.chat_orchestrator import run_chat_turn
from app.services.deps import get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/{slug}/message", response_model=ChatMessageOut)
async def send_message(
    slug: str, payload: ChatMessageIn, db: AsyncSession = Depends(get_db)
):
    """Public -- called by the embedded widget. No auth: the end user talking
    to a business's chatbot is anonymous, identified only by `session_id`
    (generated client-side). Tenant is resolved from the slug in the URL,
    e.g. kiteum.app/chat/joes-pizza -> POST /chat/joes-pizza/message."""
    tenant_id = await get_active_tenant_id_by_slug(db, slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chatbot not found")
    result = await run_chat_turn(db, tenant_id, payload.session_id, payload.message)
    return ChatMessageOut(**result)


# ---------------------------------------------------------------------------
# Admin-facing conversation history -- session list + paginated thread.
# Unchanged: authenticated via get_current_user, not a slug/tenant_id path
# param, so nothing here needs to change.
# ---------------------------------------------------------------------------

class SessionSummaryOut(BaseModel):
    session_id: str
    last_message: str
    last_message_at: object
    message_count: int


class SessionListOut(BaseModel):
    sessions: list[SessionSummaryOut]
    total: int


class ChatHistoryItemOut(BaseModel):
    role: str
    content: str
    created_at: object

    model_config = {"from_attributes": True}


class MessagesPageOut(BaseModel):
    messages: list[ChatHistoryItemOut]
    total: int


@router.get("/sessions", response_model=SessionListOut)
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions, total = await chat_crud.list_sessions(db, user.id, limit=limit, offset=offset)
    return SessionListOut(sessions=sessions, total=total)


@router.get("/sessions/{session_id}/messages", response_model=MessagesPageOut)
async def get_session_messages(
    session_id: str,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    messages, total = await chat_crud.get_messages_page(
        db, user.id, session_id, limit=limit, offset=offset
    )
    if total == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return MessagesPageOut(
        messages=[ChatHistoryItemOut.model_validate(m) for m in messages], total=total
    )


@router.get("/escalations", response_model=list[EscalatedQuestionOut])
async def list_escalations(
    status_filter: str | None = None,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    escalations = await escalation_crud.list_escalations(db, user.id, status=status_filter)
    return [EscalatedQuestionOut.model_validate(e) for e in escalations]


@router.post("/escalations/{escalation_id}/resolve", response_model=EscalatedQuestionOut)
async def resolve_escalation(
    escalation_id: uuid.UUID,
    payload: ResolveEscalationIn,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    escalation = await escalation_crud.get_escalation(db, user.id, escalation_id)
    if escalation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Escalation not found")
    escalation = await escalation_crud.resolve_escalation(db, escalation, payload.admin_answer)
    await db.commit()
    return EscalatedQuestionOut.model_validate(escalation)