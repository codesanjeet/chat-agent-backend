import os
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import LeadDB, LeadFormDB
from app.services.r2_storage import r2


# ── Lead forms (owner-managed templates) ────────────────────────────────────


DEFAULT_LEAD_FORM_SCHEMA = {
    "fields": [
        {
            "key": "name",
            "label": "Name",
            "type": "text",
            "prompt": "Could you share your name?",
            "required": False,
            "options": None,
        },
        {
            "key": "address",
            "label": "Address",
            "type": "text",
            "prompt": "What's your address?",
            "required": False,
            "options": None,
        },
        {
            "key": "email",
            "label": "Email",
            "type": "email",
            "prompt": "What's your email address?",
            "required": False,
            "options": None,
        },
        {
            "key": "phone",
            "label": "Phone number",
            "type": "phone",
            "prompt": "What's the best phone number to reach you on?",
            "required": True,
            "options": None,
        },
        {
            "key": "purpose",
            "label": "Purpose",
            "type": "textarea",
            "prompt": "What can we help you with?",
            "required": False,
            "options": None,
        },
    ]
}


async def _create_default_lead_form(db: AsyncSession, user_id: uuid.UUID) -> LeadFormDB:
    """Every new user gets a starter lead form so their chatbot can capture
    basic visitor info out of the box. Only phone number is required."""
    return await create_lead_form(
        db=db,
        user_id=user_id,
        name="General Inquiry",
        form_schema=DEFAULT_LEAD_FORM_SCHEMA,
        trigger_intent="contact, inquiry",
        is_default=True,
        requires_payment=False,
    )



async def _clear_other_defaults(
    db: AsyncSession, user_id: uuid.UUID, exclude_form_id: uuid.UUID | None = None
) -> None:
    """Only one form per tenant can be the default (the one the bot falls
    back to when no trigger_intent matches). Bulk UPDATE -- we don't need
    the ORM objects loaded here."""
    stmt = update(LeadFormDB).where(LeadFormDB.user_id == user_id, LeadFormDB.is_default.is_(True))
    if exclude_form_id is not None:
        stmt = stmt.where(LeadFormDB.id != exclude_form_id)
    await db.execute(stmt.values(is_default=False))


async def create_lead_form(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    form_schema: dict,
    trigger_intent: str | None,
    is_default: bool,
    requires_payment: bool,
) -> LeadFormDB:
    if is_default:
        await _clear_other_defaults(db, user_id)

    form = LeadFormDB(
        user_id=user_id,
        name=name,
        trigger_intent=trigger_intent,
        is_default=is_default,
        requires_payment=requires_payment,
        form_schema=form_schema,
    )
    db.add(form)
    await db.flush()
    return form


async def list_lead_forms(db: AsyncSession, user_id: uuid.UUID) -> list[LeadFormDB]:
    result = await db.execute(
        select(LeadFormDB).where(LeadFormDB.user_id == user_id).order_by(LeadFormDB.created_at.desc())
    )
    return list(result.scalars().all())


async def get_lead_form(db: AsyncSession, user_id: uuid.UUID, form_id: uuid.UUID) -> LeadFormDB | None:
    result = await db.execute(select(LeadFormDB).where(LeadFormDB.id == form_id, LeadFormDB.user_id == user_id))
    return result.scalar_one_or_none()


async def update_lead_form(
    db: AsyncSession,
    form: LeadFormDB,
    name: str | None,
    trigger_intent: str | None,
    is_default: bool | None,
    requires_payment: bool | None,
    form_schema: dict | None,
) -> LeadFormDB:
    if name is not None:
        form.name = name
    if trigger_intent is not None:
        form.trigger_intent = trigger_intent or None
    if requires_payment is not None:
        form.requires_payment = requires_payment
    if form_schema is not None:
        form.form_schema = form_schema
    if is_default is not None:
        if is_default:
            await _clear_other_defaults(db, form.user_id, exclude_form_id=form.id)
        form.is_default = is_default

    await db.flush()
    return form


async def delete_lead_form(db: AsyncSession, form: LeadFormDB) -> None:
    await db.delete(form)


# ── Public lead-form lookups (used by the chat widget, scoped by tenant) ────

async def list_active_forms_for_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> list[LeadFormDB]:
    result = await db.execute(select(LeadFormDB).where(LeadFormDB.user_id == tenant_id))
    return list(result.scalars().all())


async def get_public_lead_form(db: AsyncSession, tenant_id: uuid.UUID, form_id: uuid.UUID) -> LeadFormDB | None:
    result = await db.execute(select(LeadFormDB).where(LeadFormDB.id == form_id, LeadFormDB.user_id == tenant_id))
    return result.scalar_one_or_none()


async def get_public_lead(db: AsyncSession, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> LeadDB | None:
    """Scoped strictly to tenant_id (resolved server-side from the slug) --
    the caller never supplies a tenant id, only a slug + lead_id."""
    result = await db.execute(select(LeadDB).where(LeadDB.id == lead_id, LeadDB.user_id == tenant_id))
    return result.scalar_one_or_none()


# ── Leads (submitted data) ───────────────────────────────────────────────────

async def create_lead(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: str,
    lead_form_id: uuid.UUID | None,
    data: dict,
) -> LeadDB:
    """The widget collects the full field set client-side before posting,
    so every public submission lands as status='complete' -- 'in_progress'
    is reserved for a future incremental-save flow, unused for now.
    payment_status only gets set to 'pending' when the matched form is
    flagged requires_payment; otherwise it stays None (nothing to verify).
    Either way, submission always succeeds -- payment is never a gate."""
    payment_status = None
    if lead_form_id is not None:
        form = await db.get(LeadFormDB, lead_form_id)
        if form is not None and form.user_id == tenant_id and form.requires_payment:
            payment_status = "pending"

    lead = LeadDB(
        user_id=tenant_id,
        lead_form_id=lead_form_id,
        session_id=session_id,
        data=data,
        status="complete",
        payment_status=payment_status,
    )
    db.add(lead)
    await db.flush()
    return lead


async def list_leads(
    db: AsyncSession,
    user_id: uuid.UUID,
    status: str | None = None,
    payment_status: str | None = None,
) -> list[LeadDB]:
    query = select(LeadDB).where(LeadDB.user_id == user_id)
    if status:
        query = query.where(LeadDB.status == status)
    if payment_status:
        query = query.where(LeadDB.payment_status == payment_status)
    result = await db.execute(query.order_by(LeadDB.created_at.desc()))
    return list(result.scalars().all())


async def get_lead(db: AsyncSession, user_id: uuid.UUID, lead_id: uuid.UUID) -> LeadDB | None:
    result = await db.execute(select(LeadDB).where(LeadDB.id == lead_id, LeadDB.user_id == user_id))
    return result.scalar_one_or_none()


async def update_lead_payment_status(db: AsyncSession, lead: LeadDB, payment_status: str) -> LeadDB:
    lead.payment_status = payment_status
    await db.flush()
    return lead


async def delete_lead(db: AsyncSession, lead: LeadDB) -> None:
    await db.delete(lead)


# ── Payment proof upload (R2-backed, same bucket root as profile assets) ────

def _key_from_url(url: str | None) -> str | None:
    """Same pattern as settings_crud._key_from_url -- recovers the R2 key
    from a stored public URL so we can delete the old proof image when a
    new one replaces it."""
    base = r2.public_url("")
    if url and url.startswith(base):
        return url.replace(base, "", 1)
    return None


def _lead_proof_key(tenant_id: uuid.UUID, lead_id: uuid.UUID, filename: str) -> str:
    """Mirrors the businesses/{tenant_id}/gallery/... convention used for
    logo/payment_qr_image -- keeps every asset for a tenant under the same
    businesses/{tenant_id}/ root instead of a separate top-level tree."""
    ext = os.path.splitext(filename)[1].lower() or ".jpg"
    return f"businesses/{tenant_id}/payment_proofs/{lead_id}/{uuid.uuid4().hex}{ext}"


async def set_payment_proof(
    db: AsyncSession, lead: LeadDB, content: bytes, content_type: str, filename: str
) -> LeadDB:
    """Attaches the end user's payment screenshot to the lead. Does NOT
    verify anything -- payment_status is only ever flipped to 'verified'
    or 'rejected' by the owner via update_lead_payment_status. If a proof
    is re-uploaded after a rejection, reset status back to 'pending' so it
    shows up for review again; a 'verified' lead is left alone."""
    old_key = _key_from_url(lead.payment_proof_url)
    new_key = _lead_proof_key(lead.user_id, lead.id, filename)
    await r2.upload(new_key, content, content_type)
    new_url = r2.public_url(new_key)
    if old_key and old_key != new_key:
        await r2.delete(old_key)

    lead.payment_proof_url = new_url
    if lead.payment_status == "rejected":
        lead.payment_status = "pending"
    await db.flush()
    return lead


async def get_lead_forms_by_ids(
    db: AsyncSession, user_id: uuid.UUID, form_ids: set[uuid.UUID]
) -> dict[uuid.UUID, LeadFormDB]:
    result = await db.execute(
        select(LeadFormDB).where(LeadFormDB.user_id == user_id, LeadFormDB.id.in_(form_ids))
    )
    return {f.id: f for f in result.scalars().all()}


async def get_leads_by_session(db: AsyncSession, user_id: uuid.UUID, session_id: str) -> list[LeadDB]:
    result = await db.execute(
        select(LeadDB)
        .where(LeadDB.user_id == user_id, LeadDB.session_id == session_id)
        .order_by(LeadDB.created_at.asc())
    )
    return list(result.scalars().all())