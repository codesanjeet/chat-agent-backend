import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessageDB


async def save_message(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: str,
    role: str,
    content: str,
    gallery_items_shown: list[dict] | None = None,
    links_shown: list[dict] | None = None,
    lead_form_id: uuid.UUID | None = None,
    was_escalated: bool = False,
) -> ChatMessageDB:
    message = ChatMessageDB(
        tenant_id=tenant_id,
        session_id=session_id,
        role=role,
        content=content,
        gallery_items_shown=gallery_items_shown,
        links_shown=links_shown,
        lead_form_id=lead_form_id,
        was_escalated=was_escalated,
    )
    db.add(message)
    await db.flush()
    return message


async def get_recent_messages(
    db: AsyncSession, tenant_id: uuid.UUID, session_id: str, limit: int = 10
) -> list[ChatMessageDB]:
    result = await db.execute(
        select(ChatMessageDB)
        .where(ChatMessageDB.tenant_id == tenant_id, ChatMessageDB.session_id == session_id)
        .order_by(ChatMessageDB.created_at.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()
    return messages


async def get_full_history(db: AsyncSession, tenant_id: uuid.UUID, session_id: str) -> list[ChatMessageDB]:
    result = await db.execute(
        select(ChatMessageDB)
        .where(ChatMessageDB.tenant_id == tenant_id, ChatMessageDB.session_id == session_id)
        .order_by(ChatMessageDB.created_at.asc())
    )
    return list(result.scalars().all())


async def list_sessions(
    db: AsyncSession, tenant_id: uuid.UUID, limit: int = 20, offset: int = 0
) -> tuple[list[dict], int]:
    last_message_subq = (
        select(
            ChatMessageDB.session_id,
            ChatMessageDB.content.label("last_message"),
            ChatMessageDB.created_at.label("last_message_at"),
            func.row_number()
            .over(partition_by=ChatMessageDB.session_id, order_by=ChatMessageDB.created_at.desc())
            .label("rn"),
        )
        .where(ChatMessageDB.tenant_id == tenant_id)
        .subquery()
    )

    counts_subq = (
        select(ChatMessageDB.session_id, func.count().label("message_count"))
        .where(ChatMessageDB.tenant_id == tenant_id)
        .group_by(ChatMessageDB.session_id)
        .subquery()
    )

    query = (
        select(
            last_message_subq.c.session_id,
            last_message_subq.c.last_message,
            last_message_subq.c.last_message_at,
            counts_subq.c.message_count,
        )
        .join(counts_subq, counts_subq.c.session_id == last_message_subq.c.session_id)
        .where(last_message_subq.c.rn == 1)
        .order_by(last_message_subq.c.last_message_at.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await db.execute(query)).all()

    total = (
        await db.execute(
            select(func.count(func.distinct(ChatMessageDB.session_id))).where(
                ChatMessageDB.tenant_id == tenant_id
            )
        )
    ).scalar_one()

    sessions = [
        {
            "session_id": r.session_id,
            "last_message": r.last_message,
            "last_message_at": r.last_message_at,
            "message_count": r.message_count,
        }
        for r in rows
    ]
    return sessions, total


async def get_messages_page(
    db: AsyncSession, tenant_id: uuid.UUID, session_id: str, limit: int = 30, offset: int = 0
) -> tuple[list[ChatMessageDB], int]:
    result = await db.execute(
        select(ChatMessageDB)
        .where(ChatMessageDB.tenant_id == tenant_id, ChatMessageDB.session_id == session_id)
        .order_by(ChatMessageDB.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    messages = list(result.scalars().all())

    total = (
        await db.execute(
            select(func.count())
            .select_from(ChatMessageDB)
            .where(ChatMessageDB.tenant_id == tenant_id, ChatMessageDB.session_id == session_id)
        )
    ).scalar_one()

    return messages, total