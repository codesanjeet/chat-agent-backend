import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import app.crud.document as document_crud
from app.models.chat import EscalatedQuestionDB


async def create_escalation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: str,
    question: str,
    chat_message_id: uuid.UUID | None = None,
) -> EscalatedQuestionDB:
    escalation = EscalatedQuestionDB(
        tenant_id=tenant_id, session_id=session_id, question=question, chat_message_id=chat_message_id
    )
    db.add(escalation)
    await db.flush()
    return escalation


async def list_escalations(
    db: AsyncSession, tenant_id: uuid.UUID, status: str | None = None
) -> list[EscalatedQuestionDB]:
    query = select(EscalatedQuestionDB).where(EscalatedQuestionDB.tenant_id == tenant_id)
    if status:
        query = query.where(EscalatedQuestionDB.status == status)
    result = await db.execute(query.order_by(EscalatedQuestionDB.created_at.desc()))
    return list(result.scalars().all())


async def get_escalation(
    db: AsyncSession, tenant_id: uuid.UUID, escalation_id: uuid.UUID
) -> EscalatedQuestionDB | None:
    result = await db.execute(
        select(EscalatedQuestionDB).where(
            EscalatedQuestionDB.id == escalation_id, EscalatedQuestionDB.tenant_id == tenant_id
        )
    )
    return result.scalar_one_or_none()




# in app/curd/escalation.py
# app/curd/escalation.py

async def resolve_escalation(db, escalation, admin_answer: str) -> EscalatedQuestionDB:
    escalation.admin_answer = admin_answer
    escalation.status = "resolved"
    escalation.answered_at = datetime.now(timezone.utc)
    await db.flush()

    document = await document_crud.create_document_preview(
        db, tenant_id=escalation.tenant_id, source_type="text",
        source_label="Resolved question", extracted_text=admin_answer,
        confidence="high", reason="ok",
    )
    await document_crud.confirm_qa_document(db, escalation.tenant_id, document, escalation.question)

    return escalation
