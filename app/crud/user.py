import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserDB


def _slugify(text: str) -> str:
    """'Joe's Pizza & Grill!!' -> 'joes-pizza-grill'. Lowercase, any run of
    non-alphanumeric characters (spaces, punctuation, etc.) collapses to a
    single hyphen, leading/trailing hyphens stripped. Falls back to
    'business' if the input has no alphanumeric characters at all."""
    slug = text.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "business"


async def _first_available_slug(
    db: AsyncSession, base: str, exclude_user_id: uuid.UUID | None = None
) -> str:
    """base itself first, then base-2, base-3, ... until one's free.
    Numeric suffix keeps the slug readable ('joes-pizza-2') instead of a
    random string when the exact name is already taken by another tenant."""
    candidate = base
    suffix = 2
    while True:
        query = select(UserDB.id).where(UserDB.slug == candidate)
        if exclude_user_id is not None:
            query = query.where(UserDB.id != exclude_user_id)
        result = await db.execute(query)
        if result.scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


async def suggest_slug(
    db: AsyncSession, business_name: str, exclude_user_id: uuid.UUID | None = None
) -> str:
    base = _slugify(business_name)
    return await _first_available_slug(db, base, exclude_user_id)


async def set_custom_slug(db: AsyncSession, user: UserDB, desired_slug: str) -> str:
    """Owner-driven edit for a later dashboard-settings flow -- e.g.
    combining the suggested slug with their area or shop number. Normalizes
    the input the same way suggest_slug does, so two visually-different
    inputs can't collide silently. Raises ValueError on collision -- the
    API route calling this should turn that into a 409."""
    normalized = _slugify(desired_slug)
    query = select(UserDB.id).where(UserDB.slug == normalized, UserDB.id != user.id)
    result = await db.execute(query)
    if result.scalar_one_or_none() is not None:
        raise ValueError(f"'{normalized}' is already taken -- try adding your area or a number.")
    user.slug = normalized
    return normalized


async def complete_profile(
    db: AsyncSession,
    user: UserDB,
    business_name: str,
    business_category: str,
    slug: str | None = None,
) -> UserDB:
    user.business_name = business_name
    user.business_category = business_category
    user.profile_completed = True

    if slug:
        # Person typed something on purpose -- either it saves exactly
        # (normalized) or they find out why not. No silent fallback here.
        await set_custom_slug(db, user, slug)  # raises ValueError if taken
    elif user.slug is None:
        # No slug submitted and none set yet (e.g. resubmitted form) --
        # auto-suggest one, same as before.
        user.slug = await suggest_slug(db, business_name, exclude_user_id=user.id)

    await db.commit()
    await db.refresh(user)
    return user


# add to app/crud/user.py, near _slugify

async def get_active_tenant_id_by_slug(db: AsyncSession, slug: str) -> uuid.UUID | None:
    """Public endpoint guard, shared by any public/unauthenticated route
    that's addressed by slug (chat widget, gallery browse, etc). Returns
    None if the slug doesn't exist or the tenant is blocked -- the caller
    decides how to turn that into an HTTP response, so this stays a plain
    crud function with no FastAPI import.
    """
    result = await db.execute(select(UserDB).where(UserDB.slug == slug))
    tenant = result.scalar_one_or_none()
    if tenant is None or tenant.is_blocked:
        return None
    return tenant.id


async def get_payment_qr_by_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> str | None:
    result = await db.execute(select(UserDB.payment_qr_image).where(UserDB.id == tenant_id))
    return result.scalar_one_or_none()