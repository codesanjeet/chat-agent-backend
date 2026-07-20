import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal import UserAgreementDB



async def get_latest_agreement(
    db: AsyncSession, user_id: uuid.UUID, document_type: str
) -> UserAgreementDB | None:
    result = await db.execute(
        select(UserAgreementDB)
        .where(
            UserAgreementDB.user_id == user_id,
            UserAgreementDB.document_type == document_type,
        )
        .order_by(UserAgreementDB.accepted_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_agreement(
    db: AsyncSession,
    user_id: uuid.UUID,
    document_type: str,
    document_version: str,
    ip_address: str | None = None,
) -> UserAgreementDB:
    agreement = UserAgreementDB(
        user_id=user_id,
        document_type=document_type,
        document_version=document_version,
        accepted_at=datetime.now(timezone.utc),
        ip_address=ip_address,
    )
    db.add(agreement)
    await db.commit()
    await db.refresh(agreement)
    return agreement