"""Batch existing chunks and preserve their association with validated vectors."""

from collections.abc import Sequence

import tiktoken

from app.config import Settings
from app.integrations.embeddings import AzureEmbeddings, EMBEDDING_MODEL
from app.models import DocumentChunk, EmbeddedChunk


def embed_chunks(chunks: Sequence[DocumentChunk], settings: Settings) -> list[EmbeddedChunk]:
    if not chunks:
        return []
    provider = AzureEmbeddings(settings)
    encoding = tiktoken.get_encoding("cl100k_base")
    # Validate all inputs before sending any batch. At most 32 * 8192 tokens
    # keeps each request below the 300,000-token aggregate limit.
    for chunk in chunks:
        if not chunk.text.strip() or len(encoding.encode_ordinary(chunk.text)) > 8192:
            raise ValueError("Embedding inputs must contain text and be at most 8192 tokens each.")

    results: list[EmbeddedChunk] = []
    for start in range(0, len(chunks), settings.embedding_batch_size):
        batch = chunks[start:start + settings.embedding_batch_size]
        vectors = provider.embed([chunk.text for chunk in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            results.append(EmbeddedChunk(
                chunk=chunk, vector=vector, model=EMBEDDING_MODEL,
                deployment=provider.deployment, dimensions=provider.dimensions,
            ))
    return results
