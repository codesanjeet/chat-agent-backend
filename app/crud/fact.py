import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import BusinessFactDB


async def get_fact_by_key(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> BusinessFactDB | None:
    result = await db.execute(select(BusinessFactDB).where(BusinessFactDB.tenant_id == tenant_id, BusinessFactDB.key == key))
    return result.scalar_one_or_none()


async def list_facts(db: AsyncSession, tenant_id: uuid.UUID) -> list[BusinessFactDB]:
    result = await db.execute(select(BusinessFactDB).where(BusinessFactDB.tenant_id == tenant_id))
    return list(result.scalars().all())


async def apply_facts(
    db: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID, facts: list[dict]
) -> tuple[list[str], list[dict]]:
    """Takes the raw facts proposed by extraction (list of {"key", "value"}).

    - New key -> saved directly.
    - Existing key, same value -> no-op, nothing to save or flag.
    - Existing key, different value -> NOT saved; returned as a conflict for
      the business to resolve explicitly via resolve_facts().

    Returns (saved_keys, conflicts) where conflicts is
    [{"key", "proposed_value", "existing_value"}, ...].
    """
    saved_keys: list[str] = []
    conflicts: list[dict] = []

    for fact in facts:
        key = fact.get("key")
        value = fact.get("value")
        if not key or not value:
            continue

        existing = await get_fact_by_key(db, tenant_id, key)

        if existing is None:
            db.add(BusinessFactDB(tenant_id=tenant_id, key=key, value=value, source_document_id=document_id))
            saved_keys.append(key)
        elif existing.value.strip() == value.strip():
            pass  # identical, nothing to do
        else:
            conflicts.append({"key": key, "proposed_value": value, "existing_value": existing.value})

    await db.flush()
    return saved_keys, conflicts


async def resolve_facts(db: AsyncSession, tenant_id: uuid.UUID, resolutions: list[dict]) -> list[str]:
    """resolutions: [{"key", "proposed_value", "action": "replace"|"keep"}]
    Returns the list of keys actually replaced."""
    replaced: list[str] = []
    for r in resolutions:
        if r["action"] != "replace":
            continue
        existing = await get_fact_by_key(db, tenant_id, r["key"])
        if existing:
            existing.value = r["proposed_value"]
            replaced.append(r["key"])
    await db.flush()
    return replaced


async def delete_fact(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> bool:
    existing = await get_fact_by_key(db, tenant_id, key)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True
