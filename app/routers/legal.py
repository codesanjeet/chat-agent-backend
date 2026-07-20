from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.crud import legal as legal_crud
from app.models.user import UserDB
from app.schemas.legal import AgreementAcceptIn, AgreementOut
from app.services.deps import get_current_user

router = APIRouter(prefix="/legal", tags=["legal"])

# Bump these by hand whenever the static ToS/Privacy HTML changes meaningfully.
CURRENT_VERSIONS = {
    "tos": "v1",
    "privacy": "v1",
}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/accept", response_model=AgreementOut)
async def accept_agreement(
    payload: AgreementAcceptIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    expected_version = CURRENT_VERSIONS.get(payload.document_type)
    if expected_version is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown document type")
    if payload.document_version != expected_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Version mismatch, current is {expected_version}",
        )

    ip = _client_ip(request)
    agreement = await legal_crud.create_agreement(
        db, user.id, payload.document_type, payload.document_version, ip
    )
    return agreement


# Only needed once you have multiple doc versions and want to detect stale
# acceptances (e.g. force re-accept after a ToS update). Uncomment then, and
# uncomment get_latest_agreement in crud/legal.py + AgreementStatusOut in
# schemas/legal.py.
#
# @router.get("/status", response_model=list["AgreementStatusOut"])
# async def agreement_status(
#     db: AsyncSession = Depends(get_db),
#     user: UserDB = Depends(get_current_user),
# ):
#     results = []
#     for doc_type, current_version in CURRENT_VERSIONS.items():
#         latest = await legal_crud.get_latest_agreement(db, user.id, doc_type)
#         results.append(
#             AgreementStatusOut(
#                 document_type=doc_type,
#                 current_version=current_version,
#                 accepted=latest is not None and latest.document_version == current_version,
#                 accepted_version=latest.document_version if latest else None,
#             )
#         )
#     return results