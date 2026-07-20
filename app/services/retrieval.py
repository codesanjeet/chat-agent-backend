"""Retrieval for the chat agent -- always tenant-filtered FIRST, then ranked
by exact cosine distance within that small subset."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentChunk
from app.models.gallery import GalleryItemDB, GalleryItemType


async def search_document_chunks(
    db: AsyncSession, tenant_id: uuid.UUID, query_vector: list[float], top_k: int = 4
) -> list[DocumentChunk]:
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.tenant_id == tenant_id)
        .order_by(DocumentChunk.embedding.cosine_distance(query_vector))
        .limit(top_k)
    )
    return list(result.scalars().all())


async def search_gallery_items(
    db: AsyncSession, tenant_id: uuid.UUID, query_vector: list[float], top_k: int = 5
) -> list[dict]:
    """Rank BOTH images and folders together by embedding similarity -- a
    query can match a folder's own description ("2BHK in Patliputra") just
    as well as a specific photo's description ("balcony view").

    For any IMAGE result that belongs to a folder, also resolve that parent
    folder in the same round trip, so the caller (chat_orchestrator) can
    build "here's the best-matching photo -- and here's the folder for the
    rest of this property" without a second query later.

    Returns, in rank order:
        [{"item": GalleryItemDB, "parent_folder": GalleryItemDB | None}, ...]
    """
    result = await db.execute(
        select(GalleryItemDB)
        .where(
            GalleryItemDB.tenant_id == tenant_id,
            GalleryItemDB.embedding.is_not(None),  # folders created with no description have no embedding
        )
        .order_by(GalleryItemDB.embedding.cosine_distance(query_vector))
        .limit(top_k)
    )
    items = list(result.scalars().all())

    parent_ids = {
        item.parent_id for item in items
        if item.item_type == GalleryItemType.IMAGE and item.parent_id is not None
    }
    parents_by_id: dict[uuid.UUID, GalleryItemDB] = {}
    if parent_ids:
        presult = await db.execute(select(GalleryItemDB).where(GalleryItemDB.id.in_(parent_ids)))
        parents_by_id = {p.id: p for p in presult.scalars().all()}

    return [
        {
            "item": item,
            "parent_folder": parents_by_id.get(item.parent_id)
            if item.item_type == GalleryItemType.IMAGE and item.parent_id
            else None,
        }
        for item in items
    ]