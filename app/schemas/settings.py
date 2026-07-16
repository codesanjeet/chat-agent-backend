import uuid
from datetime import datetime

from pydantic import BaseModel,Field


class BusinessLinkIn(BaseModel):
    platform: str          # short machine key, e.g. "instagram", "whatsapp", "menu"
    url: str
    label: str | None = None


class BusinessLinkOut(BusinessLinkIn):
    pass


class LinksUpdateIn(BaseModel):
    # Full replacement, not a diff/patch -- see router docstring for why.
    links: list[BusinessLinkIn]


class ProfileUpdateIn(BaseModel):
    # All optional -- only fields explicitly provided are changed. Email is
    # intentionally absent: it's the login identity for OTP + Google, so
    # changing it needs its own re-verification flow, not a plain settings PATCH.
    business_name: str | None = None
    business_category: str | None = None
    contact_number: str | None = None


class UsageOut(BaseModel):
    """Combines UsageDB (per-tenant counters) with the tenant's PlanDB
    (shared limits). Not built via from_attributes off a single ORM row --
    the CRUD layer assembles this from a join, since 'used' and 'limit' now
    live on two different tables. See crud.get_usage_with_limits()."""

    ai_credits_used: int
    ai_credits_limit: int
    email_used: int
    email_limit: int
    ai_voice_agents_used_min: int
    ai_voice_agents_limit_min: int
    storage_used_mb: float
    storage_limit_mb: float
    embedding_tokens_used: int
    embedding_token_limit: int

    class Config:
        from_attributes = True


class SettingsOut(BaseModel):
    id: uuid.UUID
    business_name: str | None
    business_category: str | None
    email: str
    contact_number: str | None
    logo: str | None
    slug: str | None
    payment_qr_image: str | None
    profile_completed: bool
    is_configured: bool
    business_links: list[BusinessLinkOut]
    usage: UsageOut | None
    created_at: datetime

    class Config:
        from_attributes = True

class SlugUpdateIn(BaseModel):
    slug: str = Field(..., min_length=1, max_length=255)


class PublicBusinessOut(BaseModel):
    """Minimal, unauthenticated view of a tenant -- used by the public chat
    widget to render its own branding + payment QR. Deliberately excludes
    everything else on UserDB (email, contact_number, usage, etc.)."""

    business_name: str | None
    logo: str | None
    payment_qr_image: str | None