from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.crud.user import complete_profile
from app.models.user import UserDB
from app.schemas.user import CompleteProfileRequest
from app.schemas.user_auth import UserOut
from app.services.deps import get_current_user

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
    updated_user = await complete_profile(
        db,
        user=user,
        business_name=payload.business_name,
        business_category=payload.business_category,
        logo=payload.logo,
    )
    return UserOut.model_validate(updated_user)


@router.get("/profile/slug-availability")
async def check_slug_availability(
    slug: str,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    normalized = _slugify(slug)
    query = select(UserDB.id).where(UserDB.slug == normalized, UserDB.id != user.id)
    result = await db.execute(query)
    taken = result.scalar_one_or_none() is not None
    return {"slug": normalized, "available": not taken}