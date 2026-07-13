import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.crud import lead as lead_crud
from app.models.user import UserDB
from app.schemas.lead import LeadOut, LeadPaymentStatusIn, LeadSubmitIn
from app.services.deps import get_current_user

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post("/{tenant_id}/submit", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
async def submit_lead(tenant_id: uuid.UUID, payload: LeadSubmitIn, db: AsyncSession = Depends(get_db)):
    """Public -- called by the widget once the end user finishes filling the
    form the chat turn triggered (see services/chat_orchestrator.py's
    `lead_form` field in the response). Not called by the LLM directly: the
    LLM only signals WHICH form to render, the frontend collects the actual
    field values and posts them here."""
    lead = await lead_crud.create_lead(
        db, tenant_id=tenant_id, session_id=payload.session_id, lead_form_id=payload.lead_form_id, data=payload.data
    )
    await db.commit()
    return LeadOut.model_validate(lead)


@router.get("", response_model=list[LeadOut])
async def list_leads(user: UserDB = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    leads = await lead_crud.list_leads(db, user.id)
    return [LeadOut.model_validate(l) for l in leads]


@router.patch("/{lead_id}/payment-status", response_model=LeadOut)
async def update_payment_status(
    lead_id: uuid.UUID,
    payload: LeadPaymentStatusIn,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """MVP payment flow: business manually verifies the static QR payment
    (screenshot / reference the user submits) and flips this status -- no
    payment gateway integration in this pass, per your MVP scope."""
    lead = await lead_crud.get_lead(db, user.id, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    lead = await lead_crud.update_lead_payment_status(db, lead, payload.payment_status)
    await db.commit()
    return LeadOut.model_validate(lead)
