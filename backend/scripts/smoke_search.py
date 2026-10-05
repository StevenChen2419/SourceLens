"""Real Search write and direct-key readback; no question retrieval or embeddings API."""

import time
from uuid import uuid4

from azure.core.exceptions import ResourceNotFoundError
from pydantic import ValidationError

from app.config import Settings
from app.integrations.search import SearchError, SearchStore
from app.models import DocumentChunk, EmbeddedChunk


def main() -> int:
    key = "smoke-" + uuid4().hex
    try:
        settings = Settings()
        store = SearchStore(settings)
        store.ensure_index()
        chunk = DocumentChunk(document_id=key, filename="search-smoke-test.txt", page_number=1,
                              chunk_index=0, chunk_id=key, text="SourceLens Search indexing smoke test.")
        # Synthetic nonzero vector tests indexing only, without a second Azure service.
        vector = [1.0] + [0.0] * (store.dimensions - 1)
        item = EmbeddedChunk(chunk=chunk, vector=vector, dimensions=store.dimensions,
                             model="synthetic-smoke-vector", deployment="none")
        store.index_chunks([item])
        for attempt in range(10):
            try:
                document = store.inspect_chunk(key)
                break
            except ResourceNotFoundError:
                if attempt == 9:
                    raise SearchError("Write acknowledged but readback timed out; inspect the printed key manually.")
                time.sleep(1)
        for field, value in chunk.model_dump().items():
            if document.get(field) != value:
                raise SearchError("Smoke-test metadata readback did not match.")
        if document.get("embedding") != vector:
            raise SearchError("Smoke-test vector readback did not match.")
    except ValidationError:
        print("Invalid Search configuration.")
        return 1
    except SearchError as exc:
        print(f"{exc} Smoke key: {key}")
        return 1
    print(f"Success: indexed and read back {key} from {store.index_name}, dimensions={store.dimensions}.")
    print("The synthetic test record remains in the index; remove it by key after inspection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
