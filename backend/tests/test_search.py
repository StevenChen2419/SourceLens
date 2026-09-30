from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ResourceExistsError
from pydantic import ValidationError

from app.config import Settings
from app.integrations.search import SearchStore, SearchError, build_index, validate_index
from app.models import DocumentChunk, EmbeddedChunk


@pytest.fixture
def store() -> SearchStore:
    return SearchStore(Settings(_env_file=None, AZURE_SEARCH_ENDPOINT="https://example.search.windows.net", EMBEDDING_DIMENSIONS=1536))


@pytest.fixture
def index_client() -> Iterator[Mock]:
    with patch("app.integrations.search.DefaultAzureCredential"), patch("app.integrations.search.SearchIndexClient") as factory:
        yield factory.return_value.__enter__.return_value


@pytest.fixture
def client() -> Iterator[Mock]:
    with patch("app.integrations.search.DefaultAzureCredential"), patch("app.integrations.search.SearchClient") as factory:
        client = factory.return_value.__enter__.return_value
        client.upload_documents.side_effect = lambda documents: [SimpleNamespace(key=doc["chunk_id"], succeeded=True) for doc in documents]
        yield client


def chunks(count: int) -> list[EmbeddedChunk]:
    return [EmbeddedChunk(
        chunk=DocumentChunk(document_id="document", filename="file.pdf", page_number=i + 1,
                            chunk_index=i, chunk_id=f"chunk-{i}", text=f"Text {i}"),
        vector=[0.1] * 1536, dimensions=1536, model="text-embedding-3-small", deployment="embedding",
    ) for i in range(count)]


def test_schema_and_dimensions() -> None:
    index = build_index("knowledgeops-chunks", 1536)
    fields = {field.name: field for field in index.fields}
    assert fields["chunk_id"].key
    assert fields["text"].searchable
    for name in ("document_id", "filename", "page_number"):
        assert fields[name].filterable
        assert not fields[name].hidden
    assert fields["embedding"].type == "Collection(Edm.Single)"
    assert fields["embedding"].vector_search_dimensions == 1536
    assert fields["embedding"].vector_search_profile_name == index.vector_search.profiles[0].name
    assert index.vector_search.algorithms[0].parameters.metric == "cosine"
    validate_index(index, build_index(index.name, 1536))


def test_document_enumeration_filters_by_uuid_and_keeps_all_pages(store, client):
    identifier = uuid4()
    client.search.return_value = iter([{"chunk_id": str(i)} for i in range(1100)])
    assert len(store.document_chunks(identifier)) == 1100
    assert client.search.call_args.kwargs["filter"] == f"document_id eq '{identifier}'"
    assert "top" not in client.search.call_args.kwargs


def test_document_enumeration_refuses_truncated_catalog(store, client):
    client.search.return_value = iter([{}] * 10001)
    with pytest.raises(SearchError, match="10,000"):
        store.document_chunks()


def test_deletion_batches_all_known_and_discovered_keys_and_verifies_absence(store, client):
    identifier = uuid4()
    client.search.side_effect = [[{"chunk_id": "extra"}], []]
    client.delete_documents.side_effect = lambda documents: [SimpleNamespace(key=doc["chunk_id"], succeeded=True) for doc in documents]
    keys = [f"key-{i}" for i in range(205)]
    store.delete_document_chunks(identifier, keys)
    batches = [call.kwargs["documents"] for call in client.delete_documents.call_args_list]
    assert [len(batch) for batch in batches] == [100, 100, 6]
    assert {doc["chunk_id"] for batch in batches for doc in batch} == set(keys) | {"extra"}
    assert client.search.call_count == 2


@pytest.mark.parametrize("response", [[], [SimpleNamespace(key="key", succeeded=False)], [SimpleNamespace(key="wrong", succeeded=True)]])
def test_partial_or_mismatched_deletion_fails(store, client, response):
    client.search.return_value = []
    client.delete_documents.return_value = response
    with pytest.raises(SearchError, match="confirm"):
        store.delete_document_chunks(uuid4(), ["key"])


def test_deletion_visibility_delay_is_failure_not_success(store, client):
    client.search.return_value = [{"chunk_id": "key"}]
    client.delete_documents.return_value = [SimpleNamespace(key="key", succeeded=True)]
    with pytest.raises(SearchError, match="still visible"):
        store.delete_document_chunks(uuid4(), ["key"])


def test_delete_api_failure_and_missing_index(store, client):
    client.search.return_value = []
    client.delete_documents.side_effect = HttpResponseError("private")
    with pytest.raises(SearchError, match="Search deletion failed"):
        store.delete_document_chunks(uuid4(), ["key"])
    client.search.side_effect = ResourceNotFoundError()
    client.delete_documents.side_effect = ResourceNotFoundError()
    store.delete_document_chunks(uuid4(), ["key"])


def test_enumeration_failure_is_not_an_empty_result(store, client):
    client.search.side_effect = HttpResponseError("private")
    with pytest.raises(SearchError):
        store.document_chunks()


def test_create_missing_index(store: SearchStore, index_client: Mock) -> None:
    index_client.get_index.side_effect = ResourceNotFoundError()
    index_client.create_index.side_effect = lambda index: index
    store.ensure_index()
    index_client.create_index.assert_called_once()
    assert index_client.create_index.call_args.args[0].fields[-1].vector_search_dimensions == 1536


def test_existing_index_is_not_modified(store: SearchStore, index_client: Mock) -> None:
    index_client.get_index.return_value = build_index(store.index_name, 1536)
    store.ensure_index()
    index_client.create_index.assert_not_called()
    index_client.create_or_update_index.assert_not_called()
    index_client.delete_index.assert_not_called()


def test_concurrent_creation(store: SearchStore, index_client: Mock) -> None:
    index_client.get_index.side_effect = [ResourceNotFoundError(), build_index(store.index_name, 1536)]
    index_client.create_index.side_effect = ResourceExistsError()
    store.ensure_index()


@pytest.mark.parametrize("change", ["dimensions", "filter", "key", "hidden", "metric", "field"])
def test_incompatible_index_is_not_recreated(store: SearchStore, index_client: Mock, change: str) -> None:
    actual = build_index(store.index_name, 1536)
    if change == "dimensions": actual.fields[-1].vector_search_dimensions = 3
    elif change == "filter": actual.fields[1].filterable = False
    elif change == "key": actual.fields[0].key = False
    elif change == "hidden": actual.fields[2].hidden = True
    elif change == "metric": actual.vector_search.algorithms[0].parameters.metric = "euclidean"
    else: actual.fields.pop()
    index_client.get_index.return_value = actual
    with pytest.raises(SearchError, match="incompatible"):
        store.ensure_index()
    index_client.create_index.assert_not_called()
    index_client.delete_index.assert_not_called()


@pytest.mark.parametrize("count", [1, 3, 201])
def test_indexing_preserves_vectors_metadata_and_batches(store: SearchStore, client: Mock, count: int) -> None:
    source = chunks(count)
    store.index_chunks(source)
    calls = client.upload_documents.call_args_list
    assert len(calls) == (count + 99) // 100
    documents = [doc for call in calls for doc in call.kwargs["documents"]]
    assert documents == [{**item.chunk.model_dump(), "embedding": item.vector} for item in source]


@pytest.mark.parametrize("mode", ["failed", "missing", "wrong-key", "duplicate"])
def test_partial_or_invalid_results_fail(store: SearchStore, client: Mock, mode: str) -> None:
    results = [SimpleNamespace(key="chunk-0", succeeded=True), SimpleNamespace(key="chunk-1", succeeded=True)]
    if mode == "failed": results[1].succeeded = False
    elif mode == "missing": results.pop()
    elif mode == "wrong-key": results[1].key = "other"
    else: results[1].key = "chunk-0"
    client.upload_documents.side_effect = None
    client.upload_documents.return_value = results
    with pytest.raises(SearchError, match="partial"):
        store.index_chunks(chunks(3), batch_size=2)
    assert client.upload_documents.call_count == 1


def test_api_failure(store: SearchStore, client: Mock) -> None:
    client.upload_documents.side_effect = HttpResponseError("private SDK details")
    with pytest.raises(SearchError) as error:
        store.index_chunks(chunks(1))
    assert "private" not in str(error.value)


def test_index_api_failure(store: SearchStore, index_client: Mock) -> None:
    index_client.get_index.side_effect = HttpResponseError("private SDK details")
    with pytest.raises(SearchError):
        store.ensure_index()
    index_client.create_index.assert_not_called()


def test_vector_mismatch_before_writes(store: SearchStore, client: Mock) -> None:
    source = chunks(2)
    source[1].vector = [0.1]
    with pytest.raises(SearchError, match="dimensions"):
        store.index_chunks(source)
    client.upload_documents.assert_not_called()


def test_payload_size_splits_batch(store: SearchStore, client: Mock) -> None:
    source = chunks(2)
    for item in source:
        item.chunk.text = "x" * 4_100_000
    store.index_chunks(source)
    assert [len(call.kwargs["documents"]) for call in client.upload_documents.call_args_list] == [1, 1]


def test_oversized_document_fails_before_any_write(store: SearchStore, client: Mock) -> None:
    source = chunks(2)
    source[1].chunk.text = "x" * 8_000_000
    with pytest.raises(SearchError, match="payload size"):
        store.index_chunks(source)
    client.upload_documents.assert_not_called()


def test_inspect_chunk_uses_direct_key_lookup(store: SearchStore, client: Mock) -> None:
    client.get_document.return_value = {"chunk_id": "smoke-1"}
    assert store.inspect_chunk("smoke-1") == {"chunk_id": "smoke-1"}
    client.get_document.assert_called_once_with(key="smoke-1")


def test_missing_endpoint() -> None:
    with pytest.raises(SearchError, match="AZURE_SEARCH_ENDPOINT"):
        SearchStore(Settings(_env_file=None, AZURE_SEARCH_ENDPOINT="")).ensure_index()


@pytest.mark.parametrize("url", ["http://example", "https://example?key=secret", "https://example/indexes"])
def test_invalid_endpoint(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, AZURE_SEARCH_ENDPOINT=url)
