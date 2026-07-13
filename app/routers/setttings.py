import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from app.services.deps import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])

UPLOAD_DIR = Path(app_settings.LOCAL_UPLOAD_DIR)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_IMAGE_BYTES = app_settings.MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _save_image(file_bytes: bytes, original_filename: str | None) -> str:
    # NOTE: local disk for MVP -- swap for a GCS upload before deploying
    # anywhere with an ephemeral filesystem (Cloud Run wipes local files on
    # every redeploy/restart). Same pattern as the gallery image upload.
    ext = os.path.splitext(original_filename or "")[1] or ".jpg"
    filename = f"{uuid.uuid4()}{ext}"
    (UPLOAD_DIR / filename).write_bytes(file_bytes)
    return f"{app_settings.STATIC_BASE_URL}/uploads/{filename}"


def _delete_local_file(url: str | None) -> None:
    if not url:
        return
    try:
        (UPLOAD_DIR / os.path.basename(url)).unlink(missing_ok=True)
    except OSError:
        pass  # best-effort -- don't fail the request over a file that's already gone


def _validate_image(file: UploadFile, image_bytes: bytes) -> None:
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only JPEG, PNG, or WEBP images are supported")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Image too large, max {app_settings.MAX_UPLOAD_MB}MB")


async def _to_out(db: AsyncSession, user: UserDB) -> SettingsOut:
    links_record = await gallery_crud.get_business_links(db, user.id)
    links = links_record.links if links_record else []

    # get_usage_with_limits already returns a fully-built UsageOut (or None)
    # -- it joins UsageDB + PlanDB internally, so no model_validate() needed
    # here anymore like there was when usage/limits lived on one table.
    usage = await settings_crud.get_usage_with_limits(db, user.id)

    return SettingsOut(
        id=user.id,
        business_name=user.business_name,
        business_category=user.business_category,
        email=user.email,
        contact_number=user.contact_number,
        logo=user.logo,
        payment_qr_image=user.payment_qr_image,
        profile_completed=user.profile_completed,
        is_configured=user.is_configured,
        business_links=[BusinessLinkOut(**link) for link in links],
        usage=usage,
        created_at=user.created_at,
    )


@router.get("", response_model=SettingsOut)
async def get_settings(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _to_out(db, user)


@router.patch("/profile", response_model=SettingsOut)
async def update_profile(
    payload: ProfileUpdateIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
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
    await gallery_crud.upsert_business_links(db, user.id, [link.model_dump() for link in payload.links])
    await db.commit()
    await db.refresh(user)
    return await _to_out(db, user)


@router.post("/logo", response_model=SettingsOut)
async def upload_logo(
    file: UploadFile = File(...), user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    image_bytes = await file.read()
    _validate_image(file, image_bytes)

    old_logo = user.logo
    url = _save_image(image_bytes, file.filename)
    await settings_crud.set_logo(db, user, url)
    await db.commit()
    await db.refresh(user)

    _delete_local_file(old_logo)  # clean up only after the new one is safely committed
    return await _to_out(db, user)


@router.delete("/logo", response_model=SettingsOut)
async def delete_logo(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    old_logo = user.logo
    await settings_crud.set_logo(db, user, None)
    await db.commit()
    await db.refresh(user)

    _delete_local_file(old_logo)
    return await _to_out(db, user)


@router.post("/payment-qr", response_model=SettingsOut)
async def upload_payment_qr(
    file: UploadFile = File(...), user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    image_bytes = await file.read()
    _validate_image(file, image_bytes)

    old_qr = user.payment_qr_image
    url = _save_image(image_bytes, file.filename)
    await settings_crud.set_payment_qr(db, user, url)
    await db.commit()
    await db.refresh(user)

    _delete_local_file(old_qr)
    return await _to_out(db, user)


@router.delete("/payment-qr", response_model=SettingsOut)
async def delete_payment_qr(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    old_qr = user.payment_qr_image
    await settings_crud.set_payment_qr(db, user, None)
    await db.commit()
    await db.refresh(user)

    _delete_local_file(old_qr)
    return await _to_out(db, user)