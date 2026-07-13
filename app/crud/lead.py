import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import LeadDB, LeadFormDB


async def list_lead_forms(db: AsyncSession, tenant_id: uuid.UUID) -> list[LeadFormDB]:
    """Full dump, no search -- a tenant typically has a handful of forms at
    most, and the chat agent needs to see all of them (with their
    trigger_intent) to decide which one matches what the user is asking
    for."""
    result = await db.execute(select(LeadFormDB).where(LeadFormDB.user_id == tenant_id))
    return list(result.scalars().all())


async def get_lead_form(db: AsyncSession, tenant_id: uuid.UUID, form_id: uuid.UUID) -> LeadFormDB | None:
    result = await db.execute(
        select(LeadFormDB).where(LeadFormDB.id == form_id, LeadFormDB.user_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def create_lead(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: str,
    lead_form_id: uuid.UUID | None,
    data: dict,
) -> LeadDB:
    """Called once the frontend widget has collected the filled form fields
    from the end user and submits them -- NOT called by the chat LLM turn
    itself, which only signals which form to render (see chat_llm.py)."""
    lead = LeadDB(
        user_id=tenant_id,
        session_id=session_id,
        lead_form_id=lead_form_id,
        data=data,
        status="complete",
    )
    db.add(lead)
    await db.flush()
    return lead


async def list_leads(db: AsyncSession, tenant_id: uuid.UUID) -> list[LeadDB]:
    result = await db.execute(
        select(LeadDB).where(LeadDB.user_id == tenant_id).order_by(LeadDB.created_at.desc())
    )
    return list(result.scalars().all())


async def get_lead(db: AsyncSession, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> LeadDB | None:
    result = await db.execute(select(LeadDB).where(LeadDB.id == lead_id, LeadDB.user_id == tenant_id))
    return result.scalar_one_or_none()


async def update_lead_payment_status(db: AsyncSession, lead: LeadDB, payment_status: str) -> LeadDB:
    """MVP payment flow: admin manually verifies the static-QR payment and
    flips this -- "pending" | "verified" | "rejected". No payment gateway
    integration in this pass."""
    lead.payment_status = payment_status
    await db.flush()
    return lead
