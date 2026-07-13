import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatMessageDB(Base):
    """One row per message, both sides of the conversation. `content` is the
    plain reply text either way; the structured extras (gallery items shown/
    links/lead form/escalation) are only ever set on role="assistant" rows.

    `gallery_items_shown` is a JSONB SNAPSHOT, not a foreign key -- a single
    turn can surface multiple images and/or a folder link together (e.g.
    "here's the best-matching photo, and here's the folder for the rest of
    this property"), and history needs to keep showing what was suggested
    even if that image/folder is edited or deleted later. Shape:

        [
          {
            "item_id": "<uuid>",
            "item_type": "image" | "folder",
            "name": str,
            "image_url": str | None,       # only for images
            "external_url": str | None,
            "parent_folder": {              # only set when item_type == "image"
                "id": "<uuid>", "name": str, "external_url": str | None
            } | None
          },
          ...
        ]
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(String(255), index=True)

    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)

    # --- assistant-only structured extras, null on user rows ---
    gallery_items_shown: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    links_shown: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    lead_form_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_forms.id", ondelete="SET NULL"), nullable=True
    )
    was_escalated: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class EscalatedQuestionDB(Base):
    """Unchanged."""

    __tablename__ = "escalated_questions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(String(255))
    chat_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )

    question: Mapped[str] = mapped_column(Text)
    admin_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)