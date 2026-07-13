"""Embedding calls -- OpenAI text-embedding-3-small, 1536 dims.

Kept as a single choke point so budget-checking code (services/usage.py)
never needs to know which provider we use, and so we can batch calls here
without every caller having to think about it.
"""

from openai import AsyncOpenAI

from app.core.config import settings

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def embed_text(text: str) -> list[float]:
    resp = await _client.embeddings.create(model=settings.EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embed. OpenAI accepts a list input directly -- one HTTP call,
    much cheaper than N calls when confirming a document with many chunks."""
    if not texts:
        return []
    resp = await _client.embeddings.create(model=settings.EMBEDDING_MODEL, input=texts)
    # API preserves input order in resp.data
    return [item.embedding for item in resp.data]
