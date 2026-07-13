import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import PlanDB, UsageDB, UserPlanDB
from app.models.user import UserDB
from app.schemas.settings import UsageOut  # adjust import path to wherever UsageOut actually lives

# Minimum fields required before a business profile counts as "complete" --
# drives the profile_completed flag shown in onboarding/dashboard nudges.
REQUIRED_PROFILE_FIELDS = ("business_name", "business_category", "contact_number")


async def get_user(db: AsyncSession, tenant_id: uuid.UUID) -> UserDB | None:
    return await db.get(UserDB, tenant_id)


async def get_usage_with_limits(db: AsyncSession, tenant_id: uuid.UUID) -> UsageOut | None:
    """Replaces the old get_usage(). UsageDB alone no longer has the limit
    fields -- those live on PlanDB now, joined through UserPlanDB. Returns
    None if there's no usage row yet (e.g. brand-new signup mid-provisioning)
    rather than raising, so callers building SettingsOut can just show
    usage=None instead of erroring the whole settings page.

    Explicit query rather than user.usage lazy-load: that relationship has
    no lazy='selectin' on the UserDB side (only the reverse direction does),
    so accessing it lazily here under asyncio risks a MissingGreenlet error."""
    result = await db.execute(
        select(UsageDB, PlanDB)
        .join(UserPlanDB, UserPlanDB.user_id == UsageDB.user_id)
        .join(PlanDB, PlanDB.id == UserPlanDB.plan_id)
        .where(UsageDB.user_id == tenant_id)
    )
    row = result.first()
    if row is None:
        return None
    usage, plan = row

    return UsageOut(
        ai_credits_used=usage.ai_credits_used,
        ai_credits_limit=plan.ai_credits_limit,
        email_used=usage.email_used,
        email_limit=plan.email_limit,
        ai_voice_agents_used_min=usage.ai_voice_agents_used_min,
        ai_voice_agents_limit_min=plan.ai_voice_agents_limit_min,
        storage_used_mb=usage.storage_used_mb,
        storage_limit_mb=plan.storage_limit_mb,
        embedding_tokens_used=usage.embedding_tokens_used,
        embedding_token_limit=plan.embedding_token_limit,
    )


def _recompute_profile_completed(user: UserDB) -> None:
    user.profile_completed = all(
        getattr(user, field) not in (None, "") for field in REQUIRED_PROFILE_FIELDS
    )


async def update_profile(
    db: AsyncSession,
    user: UserDB,
    business_name: str | None,
    business_category: str | None,
    contact_number: int | None,
) -> UserDB:
    """Partial update -- a field is only changed if explicitly passed (not
    None). To intentionally clear business_name/business_category, pass an
    empty string rather than omitting the field."""
    if business_name is not None:
        user.business_name = business_name.strip() or None
    if business_category is not None:
        user.business_category = business_category.strip() or None
    if contact_number is not None:
        user.contact_number = contact_number

    _recompute_profile_completed(user)
    await db.flush()
    return user


async def set_logo(db: AsyncSession, user: UserDB, url: str | None) -> UserDB:
    user.logo = url
    await db.flush()
    return user


async def set_payment_qr(db: AsyncSession, user: UserDB, url: str | None) -> UserDB:
    user.payment_qr_image = url
    await db.flush()
    return user