import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import rate_limiter
from app.core.database import get_db
from app.crud import lead as lead_crud
from app.crud.user import get_active_tenant_id_by_slug, get_payment_qr_by_tenant
from app.models.user import UserDB
from app.schemas.lead import (
    LeadFormCreateIn,
    LeadFormOut,
    LeadFormUpdateIn,
    LeadOut,
    LeadPaymentStatusIn,
    LeadSubmitIn,
)
from app.services.deps import get_current_user
from app.services.image_validation import validate_and_clean_image

router = APIRouter(prefix="/leads", tags=["leads"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Public (chat widget) ─────────────────────────────────────────────────────
# All paths here start "/{slug}/..." -- the frontend only ever knows the
# slug it's mounted under, never a tenant id. tenant_id is resolved once
# per request via get_active_tenant_id_by_slug and never leaves the server.
# These never collide with the owner-only "/forms" or "/{lead_id}" routes
# below, since a literal segment ("forms" / "submit" / "payment-proof")
# always sits in a different position than a bare {lead_id} would.
#
# Every public route is rate-limited by IP -- these are unauthenticated
# and reachable by anyone, so they're the easiest target for scraping or
# a scripted hammer if left unprotected.

@router.get("/{slug}/forms", response_model=list[LeadFormOut])
async def list_public_forms(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Lets the widget fetch every form a tenant has configured so it can
    match against user intent or hand the list to the LLM to pick from."""
    ip = _client_ip(request)
    if not await rate_limiter.check(f"public_forms_ip:{ip}", max_attempts=60, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    tenant_id = await get_active_tenant_id_by_slug(db, slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    forms = await lead_crud.list_active_forms_for_tenant(db, tenant_id)
    return [LeadFormOut.model_validate(f) for f in forms]


@router.get("/{slug}/forms/{form_id}", response_model=LeadFormOut)
async def get_public_form(slug: str, form_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
    ip = _client_ip(request)
    if not await rate_limiter.check(f"public_form_detail_ip:{ip}", max_attempts=60, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    tenant_id = await get_active_tenant_id_by_slug(db, slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    form = await lead_crud.get_public_lead_form(db, tenant_id, form_id)
    if form is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form not found")
    return LeadFormOut.model_validate(form)


@router.post("/{slug}/submit", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
async def submit_lead(slug: str, payload: LeadSubmitIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Public -- called once the end user finishes filling the form the
    chat turn triggered. Submission always succeeds regardless of
    requires_payment; payment is followed up separately and never blocks
    this from completing.

    If the triggered form requires payment, the QR is attached to THIS
    same response (payment_qr_image) -- this is the exact moment the widget
    needs it, right after the user finishes the form and before showing
    any "pay now" step. No second round trip needed.
    """
    ip = _client_ip(request)
    if not await rate_limiter.check(f"lead_submit_ip:{ip}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    tenant_id = await get_active_tenant_id_by_slug(db, slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    lead = await lead_crud.create_lead(
        db, tenant_id=tenant_id, session_id=payload.session_id, lead_form_id=payload.lead_form_id, data=payload.data
    )
    await db.commit()

    lead_out = LeadOut.model_validate(lead)

    if payload.lead_form_id:
        form = await lead_crud.get_public_lead_form(db, tenant_id, payload.lead_form_id)
        if form and form.requires_payment:
            lead_out.payment_qr_image = await get_payment_qr_by_tenant(db, tenant_id)

    return lead_out


@router.post("/{slug}/{lead_id}/payment-proof", response_model=LeadOut)
async def upload_payment_proof(
    slug: str,
    lead_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Public -- called by the widget's post-submit payment step. Always
    optional: the lead already exists and is 'complete' before this is
    ever called, so a user who skips this (or never pays) still has a
    valid, submitted lead -- it just stays 'pending' until an admin
    follows up manually."""
    ip = _client_ip(request)
    if not await rate_limiter.check(f"payment_proof_ip:{ip}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    tenant_id = await get_active_tenant_id_by_slug(db, slug)
    if tenant_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    lead = await lead_crud.get_public_lead(db, tenant_id, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")

    raw_bytes = await file.read()
    clean_bytes, content_type = validate_and_clean_image(raw_bytes)

    lead = await lead_crud.set_payment_proof(db, lead, clean_bytes, content_type, file.filename or "")
    await db.commit()
    return LeadOut.model_validate(lead)


# ── Owner: lead form templates ───────────────────────────────────────────────
# Declared before the /{lead_id} routes below so "/leads/forms" resolves
# as this literal segment, not as lead_id="forms".
#
# Rate limits here are keyed by user.id, not IP: these are authenticated
# routes, but an authenticated account (or a compromised/leaked token) can
# still spam writes just as easily as an anonymous client can spam public
# ones -- auth alone doesn't cap request volume, so every route gets its
# own limiter the same as the public routes above.

@router.post("/forms", response_model=LeadFormOut, status_code=status.HTTP_201_CREATED)
async def create_form(
    payload: LeadFormCreateIn, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"create_lead_form:{user.id}", max_attempts=20, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    form = await lead_crud.create_lead_form(
        db,
        user_id=user.id,
        name=payload.name,
        form_schema=payload.form_schema.model_dump(),
        trigger_intent=payload.trigger_intent,
        is_default=payload.is_default,
        requires_payment=payload.requires_payment,
    )
    await db.commit()
    await db.refresh(form)
    return LeadFormOut.model_validate(form)


@router.get("/forms", response_model=list[LeadFormOut])
async def list_forms(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not await rate_limiter.check(f"list_forms:{user.id}", max_attempts=100, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")
    forms = await lead_crud.list_lead_forms(db, user.id)
    return [LeadFormOut.model_validate(f) for f in forms]


@router.get("/forms/{form_id}", response_model=LeadFormOut)
async def get_form(form_id: uuid.UUID, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not await rate_limiter.check(f"get_form:{user.id}", max_attempts=100, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")
    form = await lead_crud.get_lead_form(db, user.id, form_id)
    if form is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form not found")
    return LeadFormOut.model_validate(form)


@router.patch("/forms/{form_id}", response_model=LeadFormOut)
async def update_form(
    form_id: uuid.UUID,
    payload: LeadFormUpdateIn,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not await rate_limiter.check(f"update_lead_form:{user.id}", max_attempts=30, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    form = await lead_crud.get_lead_form(db, user.id, form_id)
    if form is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form not found")

    form = await lead_crud.update_lead_form(
        db,
        form,
        name=payload.name,
        trigger_intent=payload.trigger_intent,
        is_default=payload.is_default,
        requires_payment=payload.requires_payment,
        form_schema=payload.form_schema.model_dump() if payload.schema is not None else None,
    )
    await db.commit()
    await db.refresh(form)
    return LeadFormOut.model_validate(form)


@router.delete("/forms/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    form_id: uuid.UUID, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"delete_form:{user.id}", max_attempts=20, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    form = await lead_crud.get_lead_form(db, user.id, form_id)
    if form is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form not found")
    await lead_crud.delete_lead_form(db, form)
    await db.commit()


# ── Owner: submitted leads ───────────────────────────────────────────────────

@router.get("", response_model=list[LeadOut])
async def list_leads(
    status_filter: str | None = Query(None, alias="status"),
    payment_status: str | None = Query(None),
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not await rate_limiter.check(f"list_leads:{user.id}", max_attempts=100, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")
    leads = await lead_crud.list_leads(db, user.id, status=status_filter, payment_status=payment_status)
    return [LeadOut.model_validate(l) for l in leads]


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(lead_id: uuid.UUID, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not await rate_limiter.check(f"get_lead:{user.id}", max_attempts=100, window_seconds=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")
    lead = await lead_crud.get_lead(db, user.id, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    return LeadOut.model_validate(lead)


@router.patch("/{lead_id}/payment-status", response_model=LeadOut)
async def update_payment_status(
    lead_id: uuid.UUID,
    payload: LeadPaymentStatusIn,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """MVP payment flow: business manually verifies the uploaded screenshot
    against payments they've actually received and flips this status --
    no payment gateway integration in this pass."""
    if not await rate_limiter.check(f"update_payment_status:{user.id}", max_attempts=30, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    lead = await lead_crud.get_lead(db, user.id, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    lead = await lead_crud.update_lead_payment_status(db, lead, payload.payment_status)
    await db.commit()
    return LeadOut.model_validate(lead)


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lead(
    lead_id: uuid.UUID, user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not await rate_limiter.check(f"delete_lead:{user.id}", max_attempts=20, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    lead = await lead_crud.get_lead(db, user.id, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    await lead_crud.delete_lead(db, lead)
    await db.commit()