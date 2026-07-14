import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import rate_limiter
from app.core.database import get_db
from app.crud import gallery as gallery_crud
from app.crud.user import get_active_tenant_id_by_slug
from app.models.user import UserDB
from app.schemas.gallery import (
    BusinessLinksIn,
    BusinessLinksOut,
    GalleryFolderIn,
    GalleryItemOut,
    GalleryItemUpdate,
)
from app.services.deps import get_current_user
from app.services.image_validation import validate_and_clean_image

router = APIRouter(prefix="/gallery", tags=["gallery"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/folders", response_model=GalleryItemOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: GalleryFolderIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """A folder groups everything for one property / one product / one event."""
    if not await rate_limiter.check(f"create_folder:{user.id}", max_attempts=30, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    folder = await gallery_crud.create_gallery_folder(
        db, tenant_id=user.id, title=payload.title, description=payload.description,
        parent_id=payload.parent_id, external_url=payload.external_url,
    )
    await db.commit()
    return GalleryItemOut.model_validate(folder)


@router.post("/images", response_model=GalleryItemOut, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    description: str = Form(...),
    title: str | None = Form(None),
    parent_id: uuid.UUID | None = Form(None),
    external_url: str | None = Form(None),
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a single photo, optionally inside a folder. The backend uploads
    to R2 and returns the resulting public link."""
    if not await rate_limiter.check(f"upload_gallery_image:{user.id}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    raw_bytes = await file.read()
    clean_bytes, content_type = validate_and_clean_image(raw_bytes)

    image = await gallery_crud.create_gallery_image(
        db, tenant_id=user.id, file_bytes=clean_bytes, filename=file.filename,
        content_type=content_type,
        description=description, title=title, parent_id=parent_id, external_url=external_url,
    )
    await db.commit()
    return GalleryItemOut.model_validate(image)


@router.get("/{slug}/browse", response_model=list[GalleryItemOut])
async def browse_gallery_public(
    slug: str,
    request: Request,
    parent_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Public -- called by the unauthenticated Gallery.tsx browser opened
    from the chat widget. Same trust model as /chat/{slug}/message: no
    auth, anonymous visitors need to browse a business's gallery without a
    dashboard login. Tenant resolved from slug, same as the chat route."""
    ip = _client_ip(request)
    if not await rate_limiter.check(f"public_gallery_browse_ip:{ip}", max_attempts=60, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    tenant_id = await get_active_tenant_id_by_slug(db, slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gallery not found")
    items = await gallery_crud.list_gallery(db, tenant_id, parent_id=parent_id)
    return [GalleryItemOut.model_validate(i) for i in items]


@router.get("", response_model=list[GalleryItemOut])
async def browse_gallery(
    parent_id: uuid.UUID | None = None,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One level of the tree at a time, for the logged-in dashboard owner.
    Omit parent_id for gallery root; pass a folder's id to browse inside it
    -- this is how the Train AI dashboard renders the 'explore this folder'
    view."""
    if not await rate_limiter.check(f"browse_gallery:{user.id}", max_attempts=100, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    items = await gallery_crud.list_gallery(db, user.id, parent_id=parent_id)
    return [GalleryItemOut.model_validate(i) for i in items]


@router.patch("/{item_id}", response_model=GalleryItemOut)
async def update_gallery_item(
    item_id: uuid.UUID,
    payload: GalleryItemUpdate,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not await rate_limiter.check(f"update_gallery_item:{user.id}", max_attempts=30, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    item = await gallery_crud.get_gallery_item(db, user.id, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    updated = await gallery_crud.update_gallery_item(
        db, user.id, item, title=payload.title, description=payload.description, external_url=payload.external_url,
    )
    await db.commit()
    return GalleryItemOut.model_validate(updated)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gallery_item(
    item_id: uuid.UUID, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Deleting a folder deletes everything nested inside it (DB + R2)."""
    if not await rate_limiter.check(f"delete_gallery_item:{user.id}", max_attempts=20, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    item = await gallery_crud.get_gallery_item(db, user.id, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    await gallery_crud.delete_gallery_item(db, user.id, item)
    await db.commit()
    return None


@router.get("/links", response_model=BusinessLinksOut)
async def get_business_links(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not await rate_limiter.check(f"get_business_links:{user.id}", max_attempts=100, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    record = await gallery_crud.get_business_links(db, user.id)
    if record is None:
        return BusinessLinksOut(links=[])
    return BusinessLinksOut(links=record.links)


@router.put("/links", response_model=BusinessLinksOut)
async def update_business_links(
    payload: BusinessLinksIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"update_business_links:{user.id}", max_attempts=30, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    links_as_dicts = [item.model_dump() for item in payload.links]
    record = await gallery_crud.upsert_business_links(db, user.id, links_as_dicts)
    await db.commit()
    return BusinessLinksOut(links=record.links)