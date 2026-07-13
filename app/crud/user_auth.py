import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import is_expired, otp_expiry
from app.models.user import OtpCodeDB, UserDB, UserSessionDB

# --- Users -------------------------------------------------------------------


async def get_user_by_email(db: AsyncSession, email: str) -> UserDB | None:
    result = await db.execute(select(UserDB).where(UserDB.email == email.lower().strip()))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> UserDB | None:
    return await db.get(UserDB, user_id)


async def get_or_create_user_by_email(
    db: AsyncSession,
    email: str,
    google_id: str | None = None,
) -> UserDB:
    """Single source of truth for signup. Whether someone arrives via Google or
    via OTP, they end up as the same kind of row -- and if the same email uses
    both methods (e.g. OTP first, Google later), it's the same account, not a
    duplicate: we look up by email first, and only attach google_id if missing.
    """
    email = email.lower().strip()
    user = await get_user_by_email(db, email)

    if user is None:
        user = UserDB(
            email=email,
            google_id=google_id,
            google_linked_at=datetime.now(timezone.utc) if google_id else None,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    elif google_id and not user.google_id:
        user.google_id = google_id
        user.google_linked_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)

    return user


async def touch_last_login(db: AsyncSession, user: UserDB) -> None:
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()


# --- OTP -----------------------------------------------------------------------


async def get_latest_active_otp(db: AsyncSession, email: str) -> OtpCodeDB | None:
    result = await db.execute(
        select(OtpCodeDB)
        .where(OtpCodeDB.email == email, OtpCodeDB.consumed.is_(False))
        .order_by(OtpCodeDB.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_otp(db: AsyncSession, email: str, code_hash: str) -> OtpCodeDB:
    otp = OtpCodeDB(email=email, code_hash=code_hash, expires_at=otp_expiry())
    db.add(otp)
    await db.commit()
    await db.refresh(otp)
    return otp


async def register_failed_otp_attempt(db: AsyncSession, otp: OtpCodeDB) -> None:
    otp.attempts += 1
    await db.commit()


async def consume_otp(db: AsyncSession, otp: OtpCodeDB) -> None:
    otp.consumed = True
    await db.commit()


# --- Sessions (refresh-token tracking -> enables logout blacklist) -----------


async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    jti: str,
    expires_at: datetime,
    ip_address: str | None,
    user_agent: str | None,
) -> UserSessionDB:
    session = UserSessionDB(
        user_id=user_id,
        jti=jti,
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    await db.commit()
    return session


async def get_valid_session(db: AsyncSession, jti: str) -> UserSessionDB | None:
    """A session is usable only if it exists, hasn't been revoked (logout), and
    hasn't expired. This is the blacklist check performed on every /refresh."""
    result = await db.execute(select(UserSessionDB).where(UserSessionDB.jti == jti))
    session = result.scalar_one_or_none()
    if session is None or session.revoked or is_expired(session.expires_at):
        return None
    return session


async def revoke_session(db: AsyncSession, jti: str) -> None:
    result = await db.execute(select(UserSessionDB).where(UserSessionDB.jti == jti))
    session = result.scalar_one_or_none()
    if session:
        session.revoked = True
        await db.commit()


async def touch_session(db: AsyncSession, session: UserSessionDB) -> None:
    session.last_used_at = datetime.now(timezone.utc)
    await db.commit()