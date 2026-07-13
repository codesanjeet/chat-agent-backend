# """Deterministic, paragraph-aware chunking for embeddings.

# No LLM call here on purpose -- chunk boundaries are decided by simple rules,
# not a model, so this is free and instant. It's used right before embedding
# (app/crud/document.py -> confirm_document) to break a document's general
# content into units small enough to embed and search independently.

# Rules, in order:
# 1. Split on blank lines (paragraph boundaries) -- the most natural place to
#    cut, since a paragraph is usually one coherent idea.
# 2. Small consecutive paragraphs get merged together up to MAX_CHUNK_TOKENS,
#    so you don't end up with a database full of tiny, near-useless one-line
#    chunks.
# 3. Any single paragraph that's still too big on its own (longer than
#    MAX_CHUNK_TOKENS) gets split further on sentence boundaries, and if even a
#    single "sentence" is too long (e.g. a wall of text with no punctuation),
#    it's hard-split by token count as a last resort so nothing ever exceeds
#    the limit silently.
# """

# import re

# from app.core.tokenizer import count_tokens

# MAX_CHUNK_TOKENS = 300
# MIN_CHUNK_TOKENS = 40  # merge target -- avoid embedding tiny fragments alone

# _SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


# def _split_into_paragraphs(text: str) -> list[str]:
#     paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text)]
#     return [p for p in paragraphs if p]


# def _split_long_paragraph(paragraph: str) -> list[str]:
#     """A single paragraph over MAX_CHUNK_TOKENS -- split on sentences first,
#     packing sentences together up to the limit; hard-split by raw token slice
#     only as a last resort for a single run-on "sentence" with no punctuation."""
#     sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
#     if not sentences:
#         sentences = [paragraph]

#     pieces: list[str] = []
#     current = ""

#     for sentence in sentences:
#         if count_tokens(sentence) > MAX_CHUNK_TOKENS:
#             if current:
#                 pieces.append(current)
#                 current = ""
#             pieces.extend(_hard_split_by_tokens(sentence))
#             continue

#         candidate = f"{current} {sentence}".strip() if current else sentence
#         if count_tokens(candidate) <= MAX_CHUNK_TOKENS:
#             current = candidate
#         else:
#             if current:
#                 pieces.append(current)
#             current = sentence

#     if current:
#         pieces.append(current)
#     return pieces


# def _hard_split_by_tokens(text: str) -> list[str]:
#     """Last resort: no sentence punctuation to split on at all. Splits by
#     words instead of raw characters so we don't cut mid-word."""
#     words = text.split(" ")
#     pieces: list[str] = []
#     current_words: list[str] = []

#     for word in words:
#         candidate = " ".join(current_words + [word])
#         if count_tokens(candidate) <= MAX_CHUNK_TOKENS or not current_words:
#             current_words.append(word)
#         else:
#             pieces.append(" ".join(current_words))
#             current_words = [word]

#     if current_words:
#         pieces.append(" ".join(current_words))
#     return pieces


# def chunk_text(text: str) -> list[str]:
#     """Main entry point. Returns a list of chunk strings, each within
#     MAX_CHUNK_TOKENS, merging small adjacent paragraphs up toward
#     MIN_CHUNK_TOKENS so nothing tiny gets embedded on its own."""
#     if not text or not text.strip():
#         return []

#     paragraphs = _split_into_paragraphs(text)
#     if not paragraphs:
#         return []

#     chunks: list[str] = []
#     current = ""

#     for paragraph in paragraphs:
#         paragraph_tokens = count_tokens(paragraph)

#         if paragraph_tokens > MAX_CHUNK_TOKENS:
#             # Flush whatever's pending, then handle this oversized paragraph on its own.
#             if current:
#                 chunks.append(current)
#                 current = ""
#             chunks.extend(_split_long_paragraph(paragraph))
#             continue

#         candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
#         if count_tokens(candidate) <= MAX_CHUNK_TOKENS:
#             current = candidate
#         else:
#             chunks.append(current)
#             current = paragraph

#     if current:
#         chunks.append(current)

#     return chunks





"""Deterministic, paragraph-aware chunking for embeddings.

No LLM call here on purpose -- chunk boundaries are decided by simple rules,
not a model, so this is free and instant. It's used right before embedding
(app/crud/document.py -> confirm_document) to break a document's general
content into units small enough to embed and search independently.

Rules, in order:
1. Split on blank lines AND markdown structural boundaries (## headers,
   bullet points) -- our extraction prompts (extraction.py) ask Gemini to
   format output as markdown with headers/bullets, and that output often
   uses single newlines rather than blank-line-separated blocks. Without
   this, a whole document with no blank lines collapses into one paragraph
   and can end up as a single chunk.
2. Small consecutive paragraphs get merged together up to MAX_CHUNK_TOKENS,
   so you don't end up with a database full of tiny, near-useless one-line
   chunks.
3. Any single paragraph that's still too big on its own (longer than
   MAX_CHUNK_TOKENS) gets split further on sentence boundaries, and if even a
   single "sentence" is too long (e.g. a wall of text with no punctuation),
   it's hard-split by token count as a last resort so nothing ever exceeds
   the limit silently.
"""

import re

from app.core.tokenizer import count_tokens

MAX_CHUNK_TOKENS = 300
MIN_CHUNK_TOKENS = 40  # merge target -- avoid embedding tiny fragments alone

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Force a paragraph break before markdown headers and bullet/numbered list
# items so structurally-distinct sections don't get glued into one paragraph
# just because the source text lacks blank lines.
_HEADER_RE = re.compile(r"\n(?=#{1,6}\s)")
_BULLET_RE = re.compile(r"\n(?=\s*(?:[-*+]|\d+[.)])\s)")


def _split_into_paragraphs(text: str) -> list[str]:
    normalized = _HEADER_RE.sub("\n\n", text)
    normalized = _BULLET_RE.sub("\n\n", normalized)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized)]
    return [p for p in paragraphs if p]


def _split_long_paragraph(paragraph: str) -> list[str]:
    """A single paragraph over MAX_CHUNK_TOKENS -- split on sentences first,
    packing sentences together up to the limit; hard-split by raw token slice
    only as a last resort for a single run-on "sentence" with no punctuation."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
    if not sentences:
        sentences = [paragraph]

    pieces: list[str] = []
    current = ""

    for sentence in sentences:
        if count_tokens(sentence) > MAX_CHUNK_TOKENS:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_hard_split_by_tokens(sentence))
            continue

        candidate = f"{current} {sentence}".strip() if current else sentence
        if count_tokens(candidate) <= MAX_CHUNK_TOKENS:
            current = candidate
        else:
            if current:
                pieces.append(current)
            current = sentence

    if current:
        pieces.append(current)
    return pieces


def _hard_split_by_tokens(text: str) -> list[str]:
    """Last resort: no sentence punctuation to split on at all. Splits by
    words instead of raw characters so we don't cut mid-word."""
    words = text.split(" ")
    pieces: list[str] = []
    current_words: list[str] = []

    for word in words:
        candidate = " ".join(current_words + [word])
        if count_tokens(candidate) <= MAX_CHUNK_TOKENS or not current_words:
            current_words.append(word)
        else:
            pieces.append(" ".join(current_words))
            current_words = [word]

    if current_words:
        pieces.append(" ".join(current_words))
    return pieces


def chunk_text(text: str) -> list[str]:
    """Main entry point. Returns a list of chunk strings, each within
    MAX_CHUNK_TOKENS, merging small adjacent paragraphs up toward
    MIN_CHUNK_TOKENS so nothing tiny gets embedded on its own."""
    if not text or not text.strip():
        return []

    paragraphs = _split_into_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        paragraph_tokens = count_tokens(paragraph)

        if paragraph_tokens > MAX_CHUNK_TOKENS:
            # Flush whatever's pending, then handle this oversized paragraph on its own.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_paragraph(paragraph))
            continue

        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if count_tokens(candidate) <= MAX_CHUNK_TOKENS:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    # Merge a too-small trailing chunk into the previous one rather than
    # leaving an orphan fragment (e.g. a lone CTA line) as its own chunk.
    if len(chunks) > 1 and count_tokens(chunks[-1]) < MIN_CHUNK_TOKENS:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}"
        chunks.pop()

    return chunks