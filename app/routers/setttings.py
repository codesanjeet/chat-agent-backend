from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.crud import user as user_crud
from app import rate_limiter
from app.core.config import settings as app_settings
from app.core.database import get_db
from app.crud import gallery as gallery_crud
from app.crud import settings as settings_crud
from app.models.user import UserDB
from app.schemas.settings import (
    BusinessLinkOut,
    LinksUpdateIn,
    ProfileUpdateIn,
    SettingsOut,
    SlugUpdateIn
)
from app.crud import user as user_crud 
from app.schemas.settings import PublicBusinessOut  
from app.services.deps import get_current_user
from app.services.image_validation import validate_and_clean_image
router = APIRouter(prefix="/settings", tags=["settings"])

MAX_IMAGE_BYTES = app_settings.MAX_UPLOAD_MB * 1024 * 1024


async def _to_out(db: AsyncSession, user: UserDB) -> SettingsOut:
    links_record = await gallery_crud.get_business_links(db, user.id)
    links = links_record.links if links_record else []
    usage = await settings_crud.get_usage_with_limits(db, user.id)

    return SettingsOut(
        id=user.id,
        business_name=user.business_name,
        business_category=user.business_category,
        email=user.email,
        contact_number=user.contact_number,
        logo=user.logo,
        slug=user.slug,
        payment_qr_image=user.payment_qr_image,
        profile_completed=user.profile_completed,
        is_configured=user.is_configured,
        business_links=[BusinessLinkOut(**link) for link in links],
        usage=usage,
        created_at=user.created_at,
    )


@router.get("", response_model=SettingsOut)
async def get_settings(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Generous limit -- normal page loads call this often, this only
    # catches a runaway retry loop or bot account, not real usage.
    if not await rate_limiter.check(f"get_settings:{user.id}", max_attempts=100, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")
    return await _to_out(db, user)


@router.patch("/profile", response_model=SettingsOut)
async def update_profile(
    payload: ProfileUpdateIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"update_profile:{user.id}", max_attempts=20, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    await settings_crud.update_profile(
        db,
        user,
        business_name=payload.business_name,
        business_category=payload.business_category,
        contact_number=payload.contact_number,
    )
    await db.commit()
    await db.refresh(user)
    return await _to_out(db, user)


@router.put("/links", response_model=SettingsOut)
async def update_links(
    payload: LinksUpdateIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Replaces the full link set every call, not a diff -- the frontend
    already holds the complete list in state from GET /settings, so this
    avoids inventing add/remove/reorder semantics for what's really just a
    small JSONB blob. Reuses your existing gallery_crud.upsert_business_links
    rather than duplicating that logic here."""
    if not await rate_limiter.check(f"update_links:{user.id}", max_attempts=20, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    await gallery_crud.upsert_business_links(db, user.id, [link.model_dump() for link in payload.links])
    await db.commit()
    await db.refresh(user)
    return await _to_out(db, user)


@router.post("/logo", response_model=SettingsOut)
async def upload_logo(
    file: UploadFile = File(...), user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"upload_logo:{user.id}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many uploads, try again later")

    raw_bytes = await file.read()
    clean_bytes, content_type = validate_and_clean_image(raw_bytes)

    await settings_crud.set_logo_from_upload(db, user, file, raw_bytes)
    await db.commit()
    await db.refresh(user)
    return await _to_out(db, user)


@router.delete("/logo", response_model=SettingsOut)
async def delete_logo(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not await rate_limiter.check(f"delete_logo:{user.id}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    await settings_crud.clear_logo(db, user)
    await db.commit()
    await db.refresh(user)
    return await _to_out(db, user)


@router.post("/payment-qr", response_model=SettingsOut)
async def upload_payment_qr(
    file: UploadFile = File(...), user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"upload_qr:{user.id}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many uploads, try again later")

    raw_bytes = await file.read()
    clean_bytes, content_type = validate_and_clean_image(raw_bytes)

    await settings_crud.set_payment_qr_from_upload(db, user, file, raw_bytes)
    await db.commit()
    await db.refresh(user)
    return await _to_out(db, user)


@router.delete("/payment-qr", response_model=SettingsOut)
async def delete_payment_qr(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not await rate_limiter.check(f"delete_qr:{user.id}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    await settings_crud.clear_payment_qr(db, user)
    await db.commit()
    await db.refresh(user)
    return await _to_out(db, user)


# @router.patch("/slug", response_model=SettingsOut)
# async def update_slug(
#     payload: SlugUpdateIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
# ):
#     """Changing the slug breaks any previously shared chat link/QR (old URL
#     404s) and resets whatever SEO the old URL had built up. Frontend is
#     responsible for warning + confirming before calling this -- we just
#     perform the change and let set_custom_slug's ValueError become a 409
#     on collision."""
#     if not await rate_limiter.check(f"update_slug:{user.id}", max_attempts=5, window_seconds=600):
#         raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

#     try:
#         await user_crud.set_custom_slug(db, user, payload.slug)
#     except ValueError as e:
#         raise HTTPException(status.HTTP_409_CONFLICT, str(e))

#     await db.commit()
#     await db.refresh(user)
#     return await _to_out(db, user)




@router.get("/public/{slug}", response_model=PublicBusinessOut)
async def get_public_business(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Unauthenticated -- lets the chat widget fetch just enough branding
    (name, logo, payment QR) to render the payment step, without exposing
    anything else on the account. Slug in, tenant resolved server-side."""
    ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()

    if not await rate_limiter.check(f"public_business:{ip}", max_attempts=60, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    tenant_id = await user_crud.get_active_tenant_id_by_slug(db, slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    user = await settings_crud.get_user(db, tenant_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    return PublicBusinessOut(business_name=user.business_name, logo=user.logo, payment_qr_image=user.payment_qr_image)