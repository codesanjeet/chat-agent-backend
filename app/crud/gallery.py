import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokenizer import count_tokens
from app.models.gallery import BusinessLinksDB, GalleryItemDB, GalleryItemType
from app.services import usage as usage_service
from app.services.embeddings import embed_text
from app.services.extraction import moderate_gallery_description
from app.services.r2_storage import r2
from app.models.billing import UsageDB
async def _validate_parent_folder(db: AsyncSession, tenant_id: uuid.UUID, parent_id: uuid.UUID | None) -> None:
    if parent_id is None:
        return
    parent = await get_gallery_item(db, tenant_id, parent_id)
    if parent is None or parent.item_type != GalleryItemType.FOLDER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "parent_id must reference an existing folder")


async def _embed_description(db: AsyncSession, tenant_id: uuid.UUID, description: str) -> tuple[int, list[float]]:
    moderation = await moderate_gallery_description(description)
    if not moderation.is_safe:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This description can't be published: {moderation.reason or 'flagged as unsafe content'}",
        )
    token_count = count_tokens(description)
    usage_service.check_single_upload_cap(token_count)
    await usage_service.reserve_tokens(db, tenant_id, token_count)
    vector = await embed_text(description)
    return token_count, vector

# storage
async def reserve_storage(db: AsyncSession, tenant_id: uuid.UUID, size_bytes: int) -> None:
    mb = size_bytes / (1024 * 1024)
    result = await db.execute(select(UsageDB).where(UsageDB.user_id == tenant_id))
    usage = result.scalar_one()
    usage.storage_used_mb += mb
    await db.flush()


async def release_storage(db: AsyncSession, tenant_id: uuid.UUID, size_bytes: int) -> None:
    mb = size_bytes / (1024 * 1024)
    result = await db.execute(select(UsageDB).where(UsageDB.user_id == tenant_id))
    usage = result.scalar_one()
    usage.storage_used_mb = max(0.0, usage.storage_used_mb - mb)
    await db.flush()
# ── Create ───────────────────────────────────────────────────────────────────

async def create_gallery_folder(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    title: str,
    description: str,
    parent_id: uuid.UUID | None = None,
    external_url: str | None = None,
) -> GalleryItemDB:
    """A folder = one property / one product / one event. Description is
    required so the agent can recommend the whole folder ("here's the Green
    Valley listing") even before any individual photo inside has been
    searched for."""
    await _validate_parent_folder(db, tenant_id, parent_id)
    token_count, vector = await _embed_description(db, tenant_id, description)

    folder = GalleryItemDB(
        tenant_id=tenant_id,
        parent_id=parent_id,
        item_type=GalleryItemType.FOLDER,
        title=title,
        description=description,
        external_url=external_url,
        token_count=token_count,
        embedding=vector,
    )
    db.add(folder)
    await db.flush()
    return folder


async def create_gallery_image(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    description: str,
    title: str | None = None,
    parent_id: uuid.UUID | None = None,
    external_url: str | None = None,
) -> GalleryItemDB:
    """Uploads the actual file to R2, then stores the resulting public link
    + description. `filename` is only used to derive the R2 key/extension --
    it's never stored as a display field; `title` is an optional, separate
    admin-supplied caption."""
    await _validate_parent_folder(db, tenant_id, parent_id)
    token_count, vector = await _embed_description(db, tenant_id, description)

    key = r2.build_gallery_key(tenant_id, parent_id, filename)
    await reserve_storage(db, tenant_id, len(file_bytes))

    image = GalleryItemDB(
        tenant_id=tenant_id,
        parent_id=parent_id,
        item_type=GalleryItemType.IMAGE,
        title=title,
        description=description,
        r2_key=key,
        image_url=r2.public_url(key),
        external_url=external_url,
        token_count=token_count,
        embedding=vector,
    )
    db.add(image)
    await db.flush()
    return image


# ── Read ─────────────────────────────────────────────────────────────────────

async def list_gallery(
    db: AsyncSession, tenant_id: uuid.UUID, parent_id: uuid.UUID | None = None
) -> list[GalleryItemDB]:
    """One level of the tree at a time -- pass parent_id=None for gallery
    root, or a folder's id to browse inside it. Folders sort before images."""
    result = await db.execute(
        select(GalleryItemDB)
        .where(GalleryItemDB.tenant_id == tenant_id, GalleryItemDB.parent_id == parent_id)
        .order_by(GalleryItemDB.item_type, GalleryItemDB.created_at.desc())
    )
    return list(result.scalars().all())


async def get_gallery_item(db: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID) -> GalleryItemDB | None:
    result = await db.execute(
        select(GalleryItemDB).where(GalleryItemDB.id == item_id, GalleryItemDB.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def _collect_subtree(db: AsyncSession, root_id: uuid.UUID):
    """Recursive CTE: walk down from root_id and return every row in the
    subtree (root included). Used by delete to find every image r2_key and
    every token_count buried under a folder, at any depth."""
    base = (
        select(
            GalleryItemDB.id, GalleryItemDB.parent_id, GalleryItemDB.r2_key,
            GalleryItemDB.item_type, GalleryItemDB.token_count,
        )
        .where(GalleryItemDB.id == root_id)
        .cte("gallery_subtree", recursive=True)
    )
    children = select(
        GalleryItemDB.id, GalleryItemDB.parent_id, GalleryItemDB.r2_key,
        GalleryItemDB.item_type, GalleryItemDB.token_count,
    ).join(base, GalleryItemDB.parent_id == base.c.id)
    cte = base.union_all(children)

    result = await db.execute(select(cte.c.r2_key, cte.c.item_type, cte.c.token_count))
    return result.all()


# ── Update ───────────────────────────────────────────────────────────────────

async def update_gallery_item(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    item: GalleryItemDB,
    *,
    title: str | None = None,
    description: str | None = None,
    external_url: str | None = None,
) -> GalleryItemDB:
    if title is not None:
        item.title = title
    if external_url is not None:
        item.external_url = external_url

    if description is not None and description != item.description:
        if item.token_count:
            await usage_service.release_tokens(db, tenant_id, item.token_count)
        token_count, vector = await _embed_description(db, tenant_id, description)
        item.description = description
        item.token_count = token_count
        item.embedding = vector

    await db.flush()
    return item


# ── Delete ───────────────────────────────────────────────────────────────────

async def delete_gallery_item(db: AsyncSession, tenant_id: uuid.UUID, item: GalleryItemDB) -> None:
    """Deleting a folder wipes its entire subtree: every nested folder and
    image row in the DB (cascade), every image's R2 object, and releases the
    token + storage budget every description/file in that subtree was holding."""
    rows = await _collect_subtree(db, item.id)
    keys_to_purge = [r.r2_key for r in rows if r.item_type == GalleryItemType.IMAGE and r.r2_key]
    tokens_to_release = sum(r.token_count or 0 for r in rows)

    # Look up each object's size BEFORE deleting it -- head() returns {}
    # (size 0) if the object is already gone, so this degrades safely.
    bytes_to_release = 0
    for key in keys_to_purge:
        bytes_to_release += await r2.size(key)

    await db.delete(item)  # cascades to all descendant rows
    await db.flush()

    for key in keys_to_purge:
        await r2.delete(key)

    if tokens_to_release:
        await usage_service.release_tokens(db, tenant_id, tokens_to_release)
    if bytes_to_release:
        await release_storage(db, tenant_id, bytes_to_release)


# ── Business links ───────────────────────────────────────────────────────────

async def get_business_links(db: AsyncSession, tenant_id: uuid.UUID) -> BusinessLinksDB | None:
    result = await db.execute(select(BusinessLinksDB).where(BusinessLinksDB.tenant_id == tenant_id))
    return result.scalar_one_or_none()



def _normalize_links(links: list[dict]) -> list[dict]:
    """Ensures every link URL has an explicit scheme. Users often type
    'instagram.com/foo' without a protocol -- browsers/anchors need
    http(s):// or the link is treated as relative and breaks."""
    normalized = []
    for link in links:
        url = (link.get("url") or "").strip()
        if url and not url.lower().startswith(("http://", "https://")):
            url = f"https://{url}"
        normalized.append({**link, "url": url})
    return normalized

async def upsert_business_links(db: AsyncSession, tenant_id: uuid.UUID, links: list[dict]) -> BusinessLinksDB:
    """Replaces the full link set for a tenant. `links` is a plain list of
    {"platform", "url", "label"} dicts -- caller (router) is responsible for
    turning validated Pydantic items into dicts before calling this."""
    links = _normalize_links(links)

    record = await get_business_links(db, tenant_id)
    if record is None:
        record = BusinessLinksDB(tenant_id=tenant_id, links=links)
        db.add(record)
    else:
        record.links = links
    await db.flush()
    return record

