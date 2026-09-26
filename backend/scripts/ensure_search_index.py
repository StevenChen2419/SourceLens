"""Explicit idempotent creation/validation; never replaces an existing index."""

from pydantic import ValidationError
from app.config import Settings
from app.integrations.search import SearchError, SearchStore


def main() -> int:
    try:
        store = SearchStore(Settings())
        store.ensure_index()
    except ValidationError:
        print("Invalid configuration. Check Search endpoint, index name, and embedding dimensions.")
        return 1
    except SearchError as exc:
        print(str(exc))
        return 1
    print(f"Index {store.index_name} exists with a compatible {store.dimensions}-dimensional schema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
