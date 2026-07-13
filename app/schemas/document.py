import uuid
from datetime import datetime

from pydantic import BaseModel


class WeblinkIn(BaseModel):
    url: str
    source_label: str | None = None


class TextIn(BaseModel):
    text: str
    source_label: str | None = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    source_type: str
    source_label: str | None
    extracted_text: str
    confidence: str
    reason: str
    is_active: bool
    total_tokens: int
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentEditIn(BaseModel):
    """Business reviews the extracted preview and can correct it before confirming."""

    extracted_text: str


class FactConflictOut(BaseModel):
    """Returned from /confirm when a proposed fact's key already exists with
    a different value. Client shows this to the business and lets them pick,
    then posts back to /resolve-facts."""

    key: str
    proposed_value: str
    existing_value: str


class FactResolutionIn(BaseModel):
    key: str
    proposed_value: str
    action: str  # "replace" | "keep"


class ResolveFactsIn(BaseModel):
    document_id: uuid.UUID
    resolutions: list[FactResolutionIn]


class ConfirmDocumentOut(BaseModel):
    document: DocumentOut
    facts_saved: list[str]  # keys saved without conflict
    conflicts: list[FactConflictOut]  # keys needing business decision
    chunks_embedded: int
    chunks_skipped_as_duplicate: int
    tokens_used: int
    tokens_remaining: int


class BusinessFactOut(BaseModel):
    key: str
    value: str

    class Config:
        from_attributes = True
