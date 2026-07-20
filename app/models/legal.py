import uuid
from datetime import datetime,timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class UserAgreementDB(Base):
    __tablename__ = "user_agreements"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    user_email_snapshot: Mapped[str | None] = mapped_column(String, nullable=True)
    document_type: Mapped[str] = mapped_column(String)
    document_version: Mapped[str] = mapped_column(String)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)


# Not needed yet -- ToS/Privacy text lives in static HTML for now.
# Uncomment when documents need to be editable via admin panel instead of
# a redeploy. At that point, make user_agreements.document_version a real
# FK into this table instead of a free-text label.
#
# class LegalDocumentDB(Base):
#     __tablename__ = "legal_documents"
#
#     id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
#     document_type: Mapped[str] = mapped_column(String)          # "tos" | "privacy"
#     version: Mapped[str] = mapped_column(String)
#     content: Mapped[str] = mapped_column(Text)
#     is_active: Mapped[bool] = mapped_column(Boolean, default=True)
#     created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)