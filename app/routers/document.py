# before sending ot the embedding model we shoudl filer and make it thinner wiht llm 
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.ssrf_guard import assert_safe_url
from app import rate_limiter
from app.core.database import get_db
from app.crud import document as document_crud
from app.crud import fact as fact_crud
from app.models.user import UserDB
from app.schemas.document import (
    BusinessFactOut,
    ConfirmDocumentOut,
    DocumentEditIn,
    DocumentOut,
    FactConflictOut,
    ResolveFactsIn,
    TextIn,
    WeblinkIn,
)
from app.services import extraction
from app.services.deps import get_current_user
from app.services.image_validation import validate_and_clean_image
from app.services.pdf_validation import validate_pdf

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/text", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_text(
    payload: TextIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    document = await document_crud.create_document_preview(
        db, tenant_id=user.id, source_type="text", source_label=payload.source_label,
        extracted_text=payload.text, confidence="high", reason="ok",
    )
    await db.commit()
    return DocumentOut.model_validate(document)


@router.post("/image", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...), user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"upload_image:{user.id}", max_attempts=5, window_seconds=300):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    raw_bytes = await file.read()
    clean_bytes, content_type = validate_and_clean_image(raw_bytes)
    result = await extraction.extract_text_from_image(clean_bytes, content_type)
    document = await document_crud.create_document_preview(
        db, tenant_id=user.id, source_type="image", source_label=file.filename,
        extracted_text=result.text, confidence=result.confidence, reason=result.reason,
    )
    await db.commit()
    return DocumentOut.model_validate(document)


@router.post("/pdf", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    file: UploadFile = File(...), user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"upload_pdf:{user.id}", max_attempts=5, window_seconds=300):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    raw_bytes = await file.read()
    clean_bytes = validate_pdf(raw_bytes)
    result = await extraction.extract_text_from_pdf(clean_bytes)
    document = await document_crud.create_document_preview(
        db, tenant_id=user.id, source_type="pdf", source_label=file.filename,
        extracted_text=result.text, confidence=result.confidence, reason=result.reason,
    )
    await db.commit()
    return DocumentOut.model_validate(document)


@router.post("/weblink", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_weblink(
    payload: WeblinkIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"upload_weblink:{user.id}", max_attempts=5, window_seconds=300):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    final_url = payload.url
    if not final_url.startswith(("https://","http://")):
        final_url = f"https://{final_url}"

    assert_safe_url(final_url)
    result = await extraction.extract_text_from_weblink(final_url)
    document = await document_crud.create_document_preview(
        db, tenant_id=user.id, source_type="weblink", source_label=payload.source_label or final_url,
        extracted_text=result.text, confidence=result.confidence, reason=result.reason,
    )
    await db.commit()
    return DocumentOut.model_validate(document)


@router.get("", response_model=list[DocumentOut])
async def list_documents(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    documents = await document_crud.list_documents(db, user.id)
    return [DocumentOut.model_validate(d) for d in documents]


@router.patch("/{document_id}", response_model=DocumentOut)
async def edit_document(
    document_id: uuid.UUID, payload: DocumentEditIn,
    user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    document = await document_crud.get_document(db, user.id, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if document.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already confirmed -- delete and re-upload instead")
    document = await document_crud.update_document_text(db, document, payload.extracted_text)
    await db.commit()
    return DocumentOut.model_validate(document)


@router.post("/{document_id}/confirm", response_model=ConfirmDocumentOut)
async def confirm_document(
    document_id: uuid.UUID, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    document = await document_crud.get_document(db, user.id, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if document.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Document already confirmed")

    result = await document_crud.confirm_document(db, user.id, document)
    await db.commit()


    return ConfirmDocumentOut(
        document=DocumentOut.model_validate(result["document"]),
        facts_saved=result["facts_saved"],
        conflicts=[FactConflictOut(**c) for c in result["conflicts"]],
        chunks_embedded=result["chunks_embedded"],
        chunks_skipped_as_duplicate=result["chunks_skipped_as_duplicate"],
        tokens_used=result["tokens_used"],
        tokens_remaining=result["tokens_remaining"],
    )


@router.post("/resolve-facts", response_model=list[str])
async def resolve_facts(
    payload: ResolveFactsIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    resolutions = [r.model_dump() for r in payload.resolutions]
    replaced = await fact_crud.resolve_facts(db, user.id, resolutions)
    await db.commit()
    return replaced


@router.get("/facts", response_model=list[BusinessFactOut])
async def list_facts(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    facts = await fact_crud.list_facts(db, user.id)
    return [BusinessFactOut.model_validate(f) for f in facts]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    document = await document_crud.get_document(db, user.id, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await document_crud.delete_document(db, user.id, document)
    await db.commit()
    return None