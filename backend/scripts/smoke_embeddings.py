"""Explicit, billable manual smoke test; never collected by pytest."""

from pydantic import ValidationError

from app.config import Settings
from app.integrations.embeddings import (
    EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError,
)
from app.models import DocumentChunk
from app.services.embeddings import embed_chunks


def main() -> int:
    chunk = DocumentChunk(
        document_id="smoke-test", filename="smoke-test.txt", page_number=1,
        chunk_id="smoke-test-0", chunk_index=0, text="SourceLens helps people find document information.",
    )
    try:
        result = embed_chunks([chunk], Settings())[0]
    except ValidationError:
        print("Invalid configuration. Check the embedding settings documented in README.md.")
        return 1
    except (EmbeddingConfigurationError, EmbeddingAPIError, EmbeddingResponseError) as exc:
        print(str(exc))
        return 1
    print(f"Model: {result.model}; dimensions: {result.dimensions}; numeric vector validated.")
    print(f"First five values: {result.vector[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
