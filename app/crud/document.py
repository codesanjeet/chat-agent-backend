import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chunking import chunk_text , MAX_CHUNK_TOKENS
from app.core.tokenizer import count_tokens
from app.crud import fact as fact_crud
from app.models.document import Document, DocumentChunk
from app.services import usage as usage_service
from app.services.embeddings import embed_texts
from app.services.extraction import separate_facts_and_content,strip_fact_leaks



def _normalize(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _content_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Preview stage -- no embedding, no budget check beyond the single-upload cap
# ---------------------------------------------------------------------------

async def create_document_preview(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source_type: str,
    source_label: str | None,
    extracted_text: str,
    confidence: str,
    reason: str,
) -> Document:
    token_count = count_tokens(extracted_text)
    usage_service.check_single_upload_cap(token_count)  # raises 400 if over 800

    document = Document(
        tenant_id=tenant_id,
        source_type=source_type,
        source_label=source_label,
        extracted_text=extracted_text,
        confidence=confidence,
        reason=reason,
        is_active=False,
        total_tokens=0,  # only set once confirmed + embedded
    )
    db.add(document)
    await db.flush()
    return document


async def get_document(db: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID) -> Document | None:
    result = await db.execute(select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_documents(db: AsyncSession, tenant_id: uuid.UUID) -> list[Document]:
    result = await db.execute(select(Document).where(Document.tenant_id == tenant_id).order_by(Document.created_at.desc()))
    return list(result.scalars().all())


async def update_document_text(db: AsyncSession, document: Document, new_text: str) -> Document:
    """Business edits the preview before confirming. Re-checks the single-upload cap
    since edits could grow the text."""
    usage_service.check_single_upload_cap(count_tokens(new_text))
    document.extracted_text = new_text
    await db.flush()
    return document


# ---------------------------------------------------------------------------
# Confirm stage -- fact separation + chunk/dedupe/embed, budget-checked
# ---------------------------------------------------------------------------

async def confirm_document(db: AsyncSession, tenant_id: uuid.UUID, document: Document) -> dict:
    """Runs fact-separation (which also pre-splits general content into
    LLM-sized sections), saves non-conflicting facts, strips any fact text
    that leaked into a section, then dedupes + budget-checks + embeds the
    sections. Content embedding proceeds regardless of fact conflicts
    (they're independent concerns) -- conflicts are just returned for the
    business to resolve via resolve_facts()."""

    split = await separate_facts_and_content(document.extracted_text)
    saved_fact_keys, conflicts = await fact_crud.apply_facts(db, tenant_id, document.id, split.facts)

    chunks_embedded = 0
    chunks_skipped = 0
    tokens_spent = 0

    cleaned_sections = strip_fact_leaks(split.sections, split.facts)

    raw_chunks = []
    for section in cleaned_sections:
        if count_tokens(section) > MAX_CHUNK_TOKENS:
            raw_chunks.extend(chunk_text(section))  # fallback only, rarely hit
        else:
            raw_chunks.append(section)

    if raw_chunks:
        candidate_hashes = [_content_hash(c) for c in raw_chunks]
        existing_result = await db.execute(
            select(DocumentChunk.content_hash).where(
                DocumentChunk.tenant_id == tenant_id, DocumentChunk.content_hash.in_(candidate_hashes)
            )
        )
        existing_hashes = {row[0] for row in existing_result.all()}

        new_chunks = []
        seen_in_batch = set()
        for text, h in zip(raw_chunks, candidate_hashes):
            if h in existing_hashes or h in seen_in_batch:
                chunks_skipped += 1
                continue
            seen_in_batch.add(h)
            new_chunks.append((text, h))

        if new_chunks:
            token_counts = [count_tokens(t) for t, _ in new_chunks]
            total_new_tokens = sum(token_counts)

            await usage_service.reserve_tokens(db, tenant_id, total_new_tokens)

            vectors = await embed_texts([t for t, _ in new_chunks])
            for (text, h), token_count, vector in zip(new_chunks, token_counts, vectors):
                db.add(
                    DocumentChunk(
                        document_id=document.id,
                        tenant_id=tenant_id,
                        chunk_text=text,
                        content_hash=h,
                        token_count=token_count,
                        embedding=vector,
                    )
                )
            chunks_embedded = len(new_chunks)
            tokens_spent = total_new_tokens

    document.total_tokens = tokens_spent
    document.is_active = True
    await db.flush()

    remaining = await usage_service.get_remaining_tokens(db, tenant_id)

    return {
        "document": document,
        "facts_saved": saved_fact_keys,
        "conflicts": conflicts,
        "chunks_embedded": chunks_embedded,
        "chunks_skipped_as_duplicate": chunks_skipped,
        "tokens_used": tokens_spent,
        "tokens_remaining": remaining,
    }

# ---------------------------------------------------------------------------
# Delete -- releases embedding budget for whatever was actually embedded
# ---------------------------------------------------------------------------

async def delete_document(db: AsyncSession, tenant_id: uuid.UUID, document: Document) -> None:
    tokens_to_release = document.total_tokens
    await db.delete(document)  # cascades to chunks; facts keep source_document_id=NULL via ondelete=SET NULL
    await db.flush()
    if tokens_to_release:
        await usage_service.release_tokens(db, tenant_id, tokens_to_release)





async def confirm_qa_document(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    document: Document,
    question: str,
) -> dict:
    """Variant of confirm_document for escalation-resolved Q&A pairs.

    Runs the same fact-separation pass as confirm_document: if the answer
    contains a lookup-style fact (e.g. "we're open until 9pm on weekends"),
    it's saved to the facts table via apply_facts, same conflict-checking
    as any other source. Whatever general explanation remains gets chunked
    and embedded, with the question re-prepended to EVERY resulting chunk --
    not just once at the top of the source text -- so a long answer that
    gets split across chunk boundaries never produces an orphan chunk
    that's missing what question it's actually answering.

    If the whole answer turns out to be just a fact (e.g. the answer to
    "what are your weekend hours" is just "9am-9pm"), separate_facts_and_content
    returns an empty/near-empty sections list -- in that case we skip
    chunking and embedding entirely. There's nothing worth vector-searching
    when the content is already captured as an exact-match fact, so this
    also saves an embedding call that would've just stored a near-useless
    single-line chunk.
    """

    split = await separate_facts_and_content(document.extracted_text)
    saved_fact_keys, conflicts = await fact_crud.apply_facts(db, tenant_id, document.id, split.facts)

    cleaned_sections = strip_fact_leaks(split.sections, split.facts)

    chunks_embedded = 0
    chunks_skipped = 0
    tokens_spent = 0

    raw_chunks: list[str] = []
    for section in cleaned_sections:
        if count_tokens(section) > MAX_CHUNK_TOKENS:
            raw_chunks.extend(chunk_text(section))  # fallback only
        else:
            raw_chunks.append(section)

    if raw_chunks:
        contextualized_chunks = [
            f"Question: {question}\nAnswer: {c}" if not c.strip().startswith("Question:") else c
            for c in raw_chunks
        ]

        candidate_hashes = [_content_hash(c) for c in contextualized_chunks]
        existing_result = await db.execute(
            select(DocumentChunk.content_hash).where(
                DocumentChunk.tenant_id == tenant_id, DocumentChunk.content_hash.in_(candidate_hashes)
            )
        )
        existing_hashes = {row[0] for row in existing_result.all()}

        new_chunks = []
        seen_in_batch = set()
        for text, h in zip(contextualized_chunks, candidate_hashes):
            if h in existing_hashes or h in seen_in_batch:
                chunks_skipped += 1
                continue
            seen_in_batch.add(h)
            new_chunks.append((text, h))

        if new_chunks:
            token_counts = [count_tokens(t) for t, _ in new_chunks]
            total_new_tokens = sum(token_counts)
            await usage_service.reserve_tokens(db, tenant_id, total_new_tokens)

            vectors = await embed_texts([t for t, _ in new_chunks])
            for (text, h), token_count, vector in zip(new_chunks, token_counts, vectors):
                db.add(
                    DocumentChunk(
                        document_id=document.id, tenant_id=tenant_id, chunk_text=text,
                        content_hash=h, token_count=token_count, embedding=vector,
                    )
                )
            chunks_embedded = len(new_chunks)
            tokens_spent = total_new_tokens

    document.total_tokens = tokens_spent
    document.is_active = True
    await db.flush()

    return {
        "document": document,
        "facts_saved": saved_fact_keys,
        "conflicts": conflicts,
        "chunks_embedded": chunks_embedded,
        "chunks_skipped_as_duplicate": chunks_skipped,
        "tokens_used": tokens_spent,
        "tokens_remaining": await usage_service.get_remaining_tokens(db, tenant_id),
    }