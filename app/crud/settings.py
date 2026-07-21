import uuid

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import PlanDB, UsageDB, UserPlanDB
from app.models.user import UserDB
from app.schemas.settings import UsageOut  # adjust import path to wherever UsageOut actually lives
from app.services.r2_storage import r2
from .gallery import release_storage,reserve_storage

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
    contact_number: str | None,
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


# ── Logo / QR (R2-backed profile assets) ─────────────────────────────────────

def _key_from_url(url: str | None) -> str | None:
    """Recover an R2 object key from a stored public URL, e.g.
    'https://pub-xxx.r2.dev/businesses/abc/gallery/logo.png' ->
    'businesses/abc/gallery/logo.png'. Returns None if the URL doesn't
    match our public base (e.g. was never set, or points elsewhere)."""
    base = r2.public_url("")
    if url and url.startswith(base):
        return url.replace(base, "", 1)
    return None


async def _upload_profile_asset(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    asset: str,
    file: UploadFile,
    content: bytes,
    content_type: str,
    old_url: str | None,
) -> str:
    unique_asset = f"{uuid.uuid4().hex}_{asset}"
    new_key = r2.build_profile_asset_key(tenant_id, unique_asset, file.filename or "")

    old_key = _key_from_url(old_url)
    # Look up the old file's size BEFORE it's deleted, so we can net the
    # storage delta instead of just adding the new file's size on top.
    old_size = await r2.size(old_key) if old_key else 0

    await r2.upload(new_key, content, content_type)  # <-- detected type, not file.content_type
    new_url = r2.public_url(new_key)

    if old_key and old_key != new_key:
        await r2.delete(old_key)

    delta = len(content) - old_size
    if delta > 0:
        await reserve_storage(db, tenant_id, delta)
    elif delta < 0:
        await release_storage(db, tenant_id, -delta)

    return new_url


async def set_logo_from_upload(
    db: AsyncSession, user: UserDB, file: UploadFile, content: bytes, content_type: str
) -> UserDB:
    url = await _upload_profile_asset(db, user.id, "logo", file, content, content_type, old_url=user.logo)
    user.logo = url
    await db.flush()
    return user



async def clear_logo(db: AsyncSession, user: UserDB) -> UserDB:
    old_key = _key_from_url(user.logo)
    if old_key:
        old_size = await r2.size(old_key)
        await r2.delete(old_key)
        if old_size:
            await release_storage(db, user.id, old_size)
    user.logo = None
    await db.flush()
    return user


async def set_payment_qr_from_upload(
    db: AsyncSession, user: UserDB, file: UploadFile, content: bytes, content_type: str
) -> UserDB:
    url = await _upload_profile_asset(db, user.id, "qr", file, content, content_type, old_url=user.payment_qr_image)
    user.payment_qr_image = url
    await db.flush()
    return user


async def clear_payment_qr(db: AsyncSession, user: UserDB) -> UserDB:
    old_key = _key_from_url(user.payment_qr_image)
    if old_key:
        old_size = await r2.size(old_key)
        await r2.delete(old_key)
        if old_size:
            await release_storage(db, user.id, old_size)
    user.payment_qr_image = None
    await db.flush()
    return user