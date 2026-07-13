"""Token counting for embedding-budget enforcement.

We count with the same tokenizer family OpenAI's embedding models use
(cl100k_base). This is what we bill against in UsageDB.embedding_tokens_used,
so it must be computed BEFORE calling the embeddings API, not after --
otherwise we'd spend the money before knowing whether the tenant has budget
left.
"""

import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_ENCODING.encode(text))


def count_tokens_batch(texts: list[str]) -> list[int]:
    return [count_tokens(t) for t in texts]
