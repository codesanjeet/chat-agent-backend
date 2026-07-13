import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessageDB
from app.models.user import LeadDB
from app.schemas.analytics import AnalyticsOut, ConversationDayOut, LeadDayOut, ResolutionSliceOut

_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _last_n_days(days: int) -> list[datetime]:
    """Ascending list of the last `days` calendar dates (UTC), today last."""
    today = datetime.now(timezone.utc).date()
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


async def _conversations_per_day(db: AsyncSession, tenant_id: uuid.UUID, dates: list) -> dict:
    """One conversation = one session_id. A session's day is the date of its
    FIRST message ever (not first message within the window) -- so a
    returning visitor on an existing session_id is never counted again on a
    later day, even if that later day falls inside the requested range.
    """
    session_starts = (
        select(
            ChatMessageDB.session_id,
            func.min(ChatMessageDB.created_at).label("started_at"),
        )
        .where(ChatMessageDB.tenant_id == tenant_id)
        .group_by(ChatMessageDB.session_id)
        .subquery()
    )

    day_col = func.date(session_starts.c.started_at)
    rows = await db.execute(
        select(day_col.label("day"), func.count().label("conversations"))
        .select_from(session_starts)
        .where(day_col >= dates[0])
        .group_by(day_col)
    )
    counts = {row.day: row.conversations for row in rows.all()}
    return {d: counts.get(d, 0) for d in dates}


async def _leads_per_day(db: AsyncSession, tenant_id: uuid.UUID, dates: list) -> dict:
    day_col = func.date(LeadDB.created_at)
    rows = await db.execute(
        select(day_col.label("day"), func.count().label("leads"))
        .where(LeadDB.user_id == tenant_id, day_col >= dates[0])
        .group_by(day_col)
    )
    counts = {row.day: row.leads for row in rows.all()}
    return {d: counts.get(d, 0) for d in dates}


async def _resolution_breakdown(db: AsyncSession, tenant_id: uuid.UUID, since) -> tuple[int, int]:
    """Bot-answered vs escalated, also deduped to one outcome per SESSION
    (a session counts as escalated if ANY message in it was escalated),
    matching the same "one session = one conversation" rule as above.
    """
    session_outcomes = (
        select(
            ChatMessageDB.session_id,
            func.bool_or(ChatMessageDB.was_escalated).label("escalated"),
            func.min(ChatMessageDB.created_at).label("started_at"),
        )
        .where(ChatMessageDB.tenant_id == tenant_id)
        .group_by(ChatMessageDB.session_id)
        .subquery()
    )

    row = (
        await db.execute(
            select(
                func.count().filter(session_outcomes.c.escalated.is_(True)).label("escalated_count"),
                func.count().filter(session_outcomes.c.escalated.is_(False)).label("bot_count"),
            )
            .select_from(session_outcomes)
            .where(session_outcomes.c.started_at >= since)
        )
    ).one()
    return row.bot_count or 0, row.escalated_count or 0


async def get_dashboard_analytics(db: AsyncSession, tenant_id: uuid.UUID, days: int = 7) -> AnalyticsOut:
    dates = _last_n_days(days)

    conversations = await _conversations_per_day(db, tenant_id, dates)
    leads = await _leads_per_day(db, tenant_id, dates)
    bot_count, escalated_count = await _resolution_breakdown(db, tenant_id, dates[0])

    return AnalyticsOut(
        conversations=[
            ConversationDayOut(day=_DAY_LABELS[d.weekday()], conversations=conversations[d]) for d in dates
        ],
        leads=[LeadDayOut(day=_DAY_LABELS[d.weekday()], leads=leads[d]) for d in dates],
        resolution=[
            ResolutionSliceOut(name="Answered by bot", value=bot_count, color="#2B4EFF"),
            ResolutionSliceOut(name="Escalated to admin", value=escalated_count, color="#F5A623"),
        ],
    )