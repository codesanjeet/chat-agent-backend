import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.models.base import Base


class GalleryItemType(str, enum.Enum):
    FOLDER = "folder"
    IMAGE = "image"


class GalleryItemDB(Base):
    """One gallery entry -- either a FOLDER (a container: one property, one
    product, one event...) or an IMAGE (a single photo). Both carry their own
    description + embedding, so the chat agent can match a query against
    either level:

      - matches an IMAGE  -> return that one photo's link
      - matches a FOLDER  -> return the folder so the agent can say "here's
        the Green Valley Apartments listing, here are all the photos" and
        the frontend renders the folder's contents -- instead of surfacing
        one scattered photo with no idea what it belongs to.

    Folders nest via `parent_id` (adjacency list, arbitrary depth). An
    image's `parent_id` is the folder it lives in; NULL means it's a
    standalone image at gallery root. Deleting a folder cascades to
    everything under it in the DB (ondelete=CASCADE) -- the CRUD layer is
    responsible for also purging the corresponding R2 objects, since R2
    doesn't know about foreign keys.
    """

    __tablename__ = "gallery_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gallery_items.id", ondelete="CASCADE"), nullable=True, index=True
    )

    item_type: Mapped[GalleryItemType] = mapped_column(SAEnum(GalleryItemType, name="gallery_item_type"))

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Only populated on IMAGE rows -- the R2 object key and its public CDN
    # URL. FOLDER rows have neither; a folder is purely a DB/organizational
    # concept, it has no single R2 object of its own.
    r2_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Optional link to recommend alongside this item -- full listing page,
    # product page, booking link. Valid on both FOLDER and IMAGE rows.
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    token_count: Mapped[int] = mapped_column(default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    children: Mapped[list["GalleryItemDB"]] = relationship(
        "GalleryItemDB", back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped["GalleryItemDB | None"] = relationship(
        "GalleryItemDB", back_populates="children", remote_side=[id]
    )


class BusinessLinksDB(Base):
    """Unchanged from before -- one row per tenant, arbitrary external links as JSONB."""

    __tablename__ = "business_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    links: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )