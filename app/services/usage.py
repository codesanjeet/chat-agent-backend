"""Per-tenant embedding token budget.

This caps OpenAI embedding spend specifically (documents + gallery
descriptions), separate from ai_credits_used (which meters chat/LLM usage).

The limit lives on PlanDB (shared across every tenant on that plan); the
running count lives on UsageDB (per-tenant, resets each billing period).
We join through UserPlanDB to find which plan a tenant is on.

reserve_tokens() and release_tokens() both take a row lock (SELECT ... FOR
UPDATE) on the tenant's UsageDB row before reading/writing the counter, so
two concurrent uploads for the same tenant can't both pass the check and
jointly blow past the limit.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import PlanDB, UsageDB, UserPlanDB

MAX_SINGLE_UPLOAD_TOKENS = 800  # cap per individual document/gallery-image submission


class BudgetExceededError(Exception):
    def __init__(self, available: int, requested: int):
        self.available = available
        self.requested = requested
        super().__init__(f"Requested {requested} tokens, only {available} left in budget")


async def _get_plan_limit(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Look up the embedding_token_limit for whatever plan this tenant is
    currently on. No row lock needed here -- plans change rarely and are
    shared across tenants, so there's nothing to race against."""
    result = await db.execute(
        select(PlanDB.embedding_token_limit)
        .join(UserPlanDB, UserPlanDB.plan_id == PlanDB.id)
        .where(UserPlanDB.user_id == tenant_id)
    )
    limit = result.scalar_one_or_none()
    if limit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active plan found for tenant")
    return limit


async def _get_usage_for_update(db: AsyncSession, tenant_id: uuid.UUID) -> UsageDB:
    result = await db.execute(select(UsageDB).where(UsageDB.user_id == tenant_id).with_for_update())
    usage = result.scalar_one_or_none()
    if usage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usage record not found for tenant")
    return usage


def check_single_upload_cap(token_count: int) -> None:
    """Enforced right after extraction, before we even create a Document row.
    Keeps any one upload from being a huge chunk of the whole budget."""
    if token_count > MAX_SINGLE_UPLOAD_TOKENS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This content is {token_count} tokens, which is over the {MAX_SINGLE_UPLOAD_TOKENS}-token "
            f"limit for a single upload. Please split it into smaller pieces and upload them separately.",
        )


async def reserve_tokens(db: AsyncSession, tenant_id: uuid.UUID, amount: int) -> None:
    """Call this immediately before the embeddings API call, once you know
    the exact (deduped) token count you're about to spend. Raises 402 if the
    tenant doesn't have enough budget left -- nothing gets embedded in that
    case."""
    if amount <= 0:
        return
    limit = await _get_plan_limit(db, tenant_id)
    usage = await _get_usage_for_update(db, tenant_id)
    available = limit - usage.embedding_tokens_used
    if amount > available:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"You cannot embed this right now -- it needs {amount} tokens but only {max(available, 0)} "
            f"are left in your plan. Remove some existing data or upgrade your plan to add more.",
        )
    usage.embedding_tokens_used += amount
    await db.flush()


async def release_tokens(db: AsyncSession, tenant_id: uuid.UUID, amount: int) -> None:
    """Call on delete (of a document's chunks or a gallery image) to free up
    budget for reuse. Floors at 0 -- never goes negative even if counts drift."""
    if amount <= 0:
        return
    usage = await _get_usage_for_update(db, tenant_id)
    usage.embedding_tokens_used = max(0, usage.embedding_tokens_used - amount)
    await db.flush()


async def get_remaining_tokens(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    limit = await _get_plan_limit(db, tenant_id)
    result = await db.execute(select(UsageDB).where(UsageDB.user_id == tenant_id))
    usage = result.scalar_one_or_none()
    if usage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usage record not found for tenant")
    return max(0, limit - usage.embedding_tokens_used)