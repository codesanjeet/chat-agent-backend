import uuid
from datetime import datetime

from pydantic import BaseModel


class LeadSubmitIn(BaseModel):
    session_id: str
    lead_form_id: uuid.UUID | None = None
    data: dict  # filled field values, keyed to match the form's schema


class LeadOut(BaseModel):
    id: uuid.UUID
    session_id: str
    lead_form_id: uuid.UUID | None
    data: dict
    status: str
    payment_status: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class LeadPaymentStatusIn(BaseModel):
    payment_status: str  # "pending" | "verified" | "rejected"
