from collections.abc import Iterator
from unittest.mock import Mock, patch

import pytest
from azure.core.exceptions import HttpResponseError
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.dependencies import get_settings, get_search_store
from app.integrations.embeddings import EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError
from app.integrations.search import SearchStore, SearchError
from app.main import app
from app.models import RetrievedChunk
from app.services.embeddings import embed_question


def hit(number: int, score: float | None = 0.02) -> RetrievedChunk:
    return RetrievedChunk(document_id=f"doc-{number}", filename=f"file-{number}.pdf",
                          page_number=number + 1, chunk_id=f"chunk-{number}", chunk_index=number,
                          text=f"Evidence {number}", search_score=score)


@pytest.fixture
def search() -> Mock:
    return Mock(spec=SearchStore)


@pytest.fixture
def embedding() -> Iterator[Mock]:
    with patch("app.services.retrieval.embed_question", return_value=[0.1] * 1536) as mock:
        yield mock


@pytest.fixture
def client(search: Mock, embedding: Mock) -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, RETRIEVAL_TOP_K=4)
    app.dependency_overrides[get_search_store] = lambda: search
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_success_preserves_order_and_metadata(client: TestClient, search: Mock, embedding: Mock) -> None:
    results = [hit(2, 0.03), hit(0, 0.01)]
    search.hybrid_search.return_value = results
    response = client.post("/api/retrieval", json={"question": "  What is the policy?  "})
    assert response.status_code == 200
    assert response.json() == {"question": "What is the policy?", "results": [item.model_dump() for item in results]}
    assert embedding.call_args.args[0] == "What is the policy?"
    assert embedding.call_args.args[1].embedding_dimensions == 1536
    search.hybrid_search.assert_called_once_with("What is the policy?", [0.1] * 1536, 4)
    search.ensure_index.assert_not_called()
    search.index_chunks.assert_not_called()


def test_top_k_override_and_empty_results(client: TestClient, search: Mock) -> None:
    search.hybrid_search.return_value = []
    response = client.post("/api/retrieval", json={"question": "Unmatched question", "top_k": 2})
    assert response.status_code == 200
    assert response.json()["results"] == []
    assert search.hybrid_search.call_args.args[2] == 2


@pytest.mark.parametrize("body", [
    {}, {"question": ""}, {"question": " \n\t"}, {"question": None}, {"question": 3},
    {"question": "x" * 4001}, {"question": "hi", "top_k": 0},
    {"question": "hi", "top_k": 21}, {"question": "hi", "top_k": True},
], ids=["missing", "empty", "whitespace", "null", "number", "too-long", "zero-k", "large-k", "bool-k"])
def test_invalid_request_never_calls_azure(client: TestClient, search: Mock, embedding: Mock, body: dict) -> None:
    assert client.post("/api/retrieval", json=body).status_code == 422
    embedding.assert_not_called()
    search.hybrid_search.assert_not_called()


@pytest.mark.parametrize("error", [EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError])
def test_embedding_failures_are_safe(client: TestClient, search: Mock, embedding: Mock, error: type[Exception]) -> None:
    embedding.side_effect = error("private SDK text")
    response = client.post("/api/retrieval", json={"question": "policy"})
    assert response.status_code == 503
    assert "private" not in response.text
    search.hybrid_search.assert_not_called()


def test_search_failure_is_safe(client: TestClient, search: Mock) -> None:
    search.hybrid_search.side_effect = SearchError("private SDK text")
    response = client.post("/api/retrieval", json={"question": "policy"})
    assert response.status_code == 503
    assert "private" not in response.text


def test_question_uses_existing_embedding_provider() -> None:
    settings = Settings(_env_file=None)
    with patch("app.services.embeddings.AzureEmbeddings") as provider:
        provider.return_value.embed.return_value = [[0.1] * 1536]
        assert embed_question("policy", settings) == [0.1] * 1536
        provider.assert_called_once_with(settings)
        provider.return_value.embed.assert_called_once_with(["policy"])


def test_over_token_limit_question_rejected_before_provider() -> None:
    with patch("app.services.embeddings.AzureEmbeddings") as provider:
        with pytest.raises(ValueError, match="8192"):
            embed_question(" word" * 8193, Settings(_env_file=None))
        provider.assert_not_called()


@pytest.mark.parametrize("top_k", [0, 21])
def test_invalid_config(top_k: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, RETRIEVAL_TOP_K=top_k)


@pytest.fixture
def sdk() -> Iterator[Mock]:
    with patch("app.integrations.search.DefaultAzureCredential"), patch("app.integrations.search.SearchClient") as factory:
        yield factory.return_value.__enter__.return_value


def store() -> SearchStore:
    return SearchStore(Settings(_env_file=None, AZURE_SEARCH_ENDPOINT="https://example.search.windows.net", EMBEDDING_DIMENSIONS=1536))


def test_single_hybrid_sdk_query_and_mapping(sdk: Mock) -> None:
    records = [hit(3, 0.04), hit(1, None)]
    sdk.search.return_value = [
        {**item.model_dump(exclude={"search_score"}), "@search.score": item.search_score} for item in records
    ]
    vector = [0.1] * 1536
    assert store().hybrid_search("policy", vector, 2) == records
    sdk.search.assert_called_once()
    args = sdk.search.call_args.kwargs
    assert args["search_text"] == "policy"
    assert args["search_fields"] == ["text"]
    assert args["top"] == 2
    assert args["query_type"] == "simple"
    assert len(args["vector_queries"]) == 1
    query = args["vector_queries"][0]
    assert query.vector == vector and query.fields == "embedding"
    assert query.k_nearest_neighbors == 50
    assert "embedding" not in args["select"]
    assert "semantic_configuration_name" not in args


def test_sdk_empty_results(sdk: Mock) -> None:
    sdk.search.return_value = []
    assert store().hybrid_search("policy", [0.1] * 1536, 5) == []


@pytest.mark.parametrize("lazy", [False, True])
def test_sdk_failure_including_lazy_iteration(sdk: Mock, lazy: bool) -> None:
    def broken_results() -> Iterator[dict]:
        yield hit(1).model_dump()
        raise HttpResponseError("private SDK details")
    if lazy:
        sdk.search.return_value = broken_results()
    else:
        sdk.search.side_effect = HttpResponseError("private SDK details")
    with pytest.raises(SearchError, match="retrieval failed") as error:
        store().hybrid_search("policy", [0.1] * 1536, 5)
    assert "private" not in str(error.value)


def test_incomplete_citation_metadata_is_rejected(sdk: Mock) -> None:
    sdk.search.return_value = [{"text": "Missing source metadata"}]
    with pytest.raises(SearchError, match="invalid chunk metadata"):
        store().hybrid_search("policy", [0.1] * 1536, 5)


def test_vector_dimensions_checked_before_search(sdk: Mock) -> None:
    with pytest.raises(SearchError, match="dimensions"):
        store().hybrid_search("policy", [0.1], 5)
    sdk.search.assert_not_called()
