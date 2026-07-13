import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class GalleryItemType(str, Enum):
    folder = "folder"
    image = "image"


class GalleryFolderIn(BaseModel):
    title: str
    description: str
    parent_id: uuid.UUID | None = None
    external_url: str | None = None


class GalleryItemUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    external_url: str | None = None


class GalleryItemOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    item_type: GalleryItemType
    title: str | None
    description: str
    image_url: str | None  # only set for images
    external_url: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class BusinessLinkItem(BaseModel):
    platform: str  # short machine key, e.g. "instagram", "whatsapp", "menu", "booking", "website"
    url: str
    label: str | None = None  # human-readable text the LLM can use directly, e.g. "Follow us on Instagram"


class BusinessLinksIn(BaseModel):
    links: list[BusinessLinkItem]


class BusinessLinksOut(BaseModel):
    links: list[BusinessLinkItem]