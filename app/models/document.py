import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.models.base import Base


class Document(Base):
    """One uploaded source (text paste / image / PDF / weblink) -- the parent
    record holding the full extracted text, used for the preview/edit step.
    The actual embedded, searchable units live in DocumentChunk (one-to-many);
    Document itself is never embedded or searched directly.

    is_active only flips True once the business has confirmed the preview,
    resolved any fact conflicts, and the content passed moderation + fit the
    token budget. Nothing before that is used by the chatbot.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    source_type: Mapped[str] = mapped_column(String(20))  # "text" | "image" | "pdf" | "weblink"
    source_label: Mapped[str | None] = mapped_column(String(500), nullable=True)  # filename or URL

    extracted_text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(10))  # "high" | "low"
    reason: Mapped[str] = mapped_column(String(50))  # "ok" | "blurry" | "empty" | ...

    is_active: Mapped[bool] = mapped_column(default=False)
    total_tokens: Mapped[int] = mapped_column(default=0)  # sum of this doc's chunk tokens, once confirmed

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    """One embedded, independently-searchable unit of a Document. Chunk
    boundaries come from paragraph-aware splitting (app/core/chunking.py), not
    an LLM call -- deterministic and free. content_hash enables per-tenant
    exact-duplicate detection so the same content is never embedded (and
    billed) twice.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("tenant_id", "content_hash", name="uq_chunk_tenant_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    chunk_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256 of normalized text
    token_count: Mapped[int] = mapped_column()

    embedding: Mapped[list[float]] = mapped_column(Vector(settings.EMBEDDING_DIM))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped["Document"] = relationship(back_populates="chunks")


class BusinessFactDB(Base):
    """Short, volatile, exact-lookup facts (hours, phone, pricing, policies).
    Deliberately NOT embedded/vector-searched: a question like "what time do
    you close" needs the current exact answer, not a semantically-similar
    chunk that might be stale. Injected directly into the chat system prompt
    at request time instead -- see the (future) chat endpoint.

    One row per (tenant, key) -- a re-submitted fact with the same key is a
    conflict the business must explicitly resolve (replace or keep existing),
    never silently overwritten.
    """

    __tablename__ = "business_facts"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_fact_tenant_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    key: Mapped[str] = mapped_column(String(100))    # e.g. "opening_hours"
    value: Mapped[str] = mapped_column(Text)          # e.g. "Mon-Sat 9am-8pm, closed Sunday"

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
