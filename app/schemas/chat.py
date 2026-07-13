import uuid
from datetime import datetime

from pydantic import BaseModel


class ChatMessageIn(BaseModel):
    session_id: str
    message: str


class GalleryParentFolderRef(BaseModel):
    id: uuid.UUID
    title: str | None = None
    external_url: str | None = None


class GalleryItemRef(BaseModel):
    item_id: uuid.UUID
    item_type: str
    title: str | None = None
    image_url: str | None = None
    external_url: str | None = None
    parent_folder: GalleryParentFolderRef | None = None


class LinkRef(BaseModel):
    platform: str
    url: str
    label: str | None = None


class LeadFormRef(BaseModel):
    id: uuid.UUID
    name: str
    requires_payment: bool
    form_schema: dict


class ChatMessageOut(BaseModel):
    reply: str
    gallery_items: list[GalleryItemRef] = []  # was: image: GalleryImageRef | None
    links: list[LinkRef] = []
    lead_form: LeadFormRef | None = None
    escalated: bool = False


class ChatHistoryItem(BaseModel):
    role: str
    content: str
    gallery_items_shown: list[GalleryItemRef] | None = None  # new
    links_shown: list[LinkRef] | None = None  # new
    was_escalated: bool = False  # new -- was missing here even before
    created_at: datetime

    class Config:
        from_attributes = True


class EscalatedQuestionOut(BaseModel):
    id: uuid.UUID
    session_id: str
    question: str
    admin_answer: str | None
    status: str
    created_at: datetime
    answered_at: datetime | None

    class Config:
        from_attributes = True


class ResolveEscalationIn(BaseModel):
    admin_answer: str