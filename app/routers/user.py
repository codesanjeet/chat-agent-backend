from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import rate_limiter
from app.core.database import get_db
from app.crud.user import _slugify, complete_profile
from app.models.user import UserDB
from app.schemas.user import CompleteProfileRequest
from app.schemas.user_auth import UserOut
from app.services.deps import get_current_user
from sqlalchemy import select
from app.crud import legal as legal_crud
from app.routers.legal import CURRENT_VERSIONS
router = APIRouter(prefix="/user", tags=["profile"])




@router.put("/profile/complete", response_model=UserOut)
async def complete_profile_endpoint(
    payload: CompleteProfileRequest,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """First required step after signup. Sets profile_completed=True so the
    frontend route guard can stop redirecting to /profile. Does NOT touch
    is_configured -- that only flips true after onboarding (knowledge upload +
    lead form) completes, handled by a separate endpoint later."""
    for doc_type, expected_version in CURRENT_VERSIONS.items():
        agreement = await legal_crud.get_latest_agreement(db, user.id, doc_type)
        if agreement is None or agreement.document_version != expected_version:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"You need to accept the current {doc_type} before continuing.",
            )

    updated_user = await complete_profile(
        db,
        user=user,
        business_name=payload.business_name,
        business_category=payload.business_category,
    )
    return UserOut.model_validate(updated_user)


@router.get("/profile/slug-availability")
async def check_slug_availability(
    slug: str,
    request: Request,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not await rate_limiter.check(f"slug_check:{user.id}", max_attempts=30, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    normalized = _slugify(slug)
    query = select(UserDB.id).where(UserDB.slug == normalized, UserDB.id != user.id)
    result = await db.execute(query)
    taken = result.scalar_one_or_none() is not None
    return {"slug": normalized, "available": not taken}








# --- Add this import near your other crud imports at the top of settings.py ---
from app.crud import analytics as analytics_crud
from app.schemas.analytics import AnalyticsOut


# --- Add this route anywhere in the router, e.g. after get_settings() ---
@router.get("/analytics", response_model=AnalyticsOut)
async def get_analytics(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Same generous ceiling as GET /settings -- the dashboard polls this on
    # load, not something a user triggers repeatedly by hand.
    if not await rate_limiter.check(f"get_analytics:{user.id}", max_attempts=100, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")
    return await analytics_crud.get_dashboard_analytics(db, user.id)

# Nothing else in settings.py changes -- the commented-out /settings/slug
# route is left exactly as-is.