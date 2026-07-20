import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

FieldType = Literal["text", "textarea", "number", "email", "phone", "date", "select"]
PaymentStatus = Literal["pending", "verified", "rejected"]


class LeadFormField(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)       # matched against `data` keys on submit
    label: str = Field(..., min_length=1, max_length=255)      # shown on the rendered form
    type: FieldType = "text"
    prompt: str = Field(..., min_length=1, max_length=500)     # what the bot says to ask for it
    required: bool = False
    options: list[str] | None = None                           # only used when type == "select"

    @field_validator("options")
    @classmethod
    def _options_only_for_select(cls, v, info):
        if v and info.data.get("type") != "select":
            raise ValueError("options is only valid when type is 'select'")
        return v


class LeadFormSchema(BaseModel):
    fields: list[LeadFormField] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def _unique_keys(cls, v):
        keys = [f.key for f in v]
        if len(keys) != len(set(keys)):
            raise ValueError("field keys must be unique within a form")
        return v


class LeadFormCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    trigger_intent: str | None = None
    is_default: bool = False
    requires_payment: bool = False
    form_schema: LeadFormSchema


class LeadFormUpdateIn(BaseModel):
    """Partial update -- omit a field to leave it unchanged. To clear
    trigger_intent, pass an empty string rather than omitting it."""

    name: str | None = Field(None, min_length=1, max_length=255)
    trigger_intent: str | None = None
    is_default: bool | None = None
    requires_payment: bool | None = None
    form_schema: LeadFormSchema | None = None


class LeadFormOut(BaseModel):
    id: uuid.UUID
    name: str
    trigger_intent: str | None
    is_default: bool
    requires_payment: bool
    form_schema: dict
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LeadSubmitIn(BaseModel):
    session_id: str
    lead_form_id: uuid.UUID | None = None
    data: dict 


class LeadOut(BaseModel):
    id: uuid.UUID
    session_id: str
    lead_form_id: uuid.UUID | None
    data: dict
    status: str
    payment_status: str | None
    payment_qr_image: str | None = None 
    payment_proof_url: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class LeadPaymentStatusIn(BaseModel):
    payment_status: PaymentStatus