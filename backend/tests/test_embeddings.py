from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError
from openai import APIConnectionError, RateLimitError
from pydantic import ValidationError

from app.config import Settings
from app.integrations.embeddings import (
    AzureEmbeddings, EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError,
)
from app.models import DocumentChunk
from app.services.embeddings import embed_chunks


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None, AZURE_EMBEDDING_ENDPOINT="https://example.openai.azure.com/openai/v1/",
        AZURE_EMBEDDING_DEPLOYMENT="small-embedding", EMBEDDING_DIMENSIONS=3, EMBEDDING_BATCH_SIZE=2,
    )


@pytest.fixture
def sdk() -> Iterator[Mock]:
    with patch("app.integrations.embeddings.DefaultAzureCredential"), patch(
        "app.integrations.embeddings.get_bearer_token_provider", return_value=lambda: "test-token"
    ), patch("app.integrations.embeddings.OpenAI") as factory:
        client = factory.return_value.__enter__.return_value
        yield client


def chunks(count: int) -> list[DocumentChunk]:
    return [DocumentChunk(
        document_id="document", filename="source.pdf", page_number=index + 1,
        chunk_id=f"chunk-{index}", chunk_index=index, text=f"Source text {index}",
    ) for index in range(count)]


def response(count: int, *, dimensions: int = 3) -> SimpleNamespace:
    return SimpleNamespace(model="text-embedding-3-small", data=[
        SimpleNamespace(index=index, embedding=[float(index + 1)] * dimensions)
        for index in range(count)
    ])


def test_single_chunk(settings: Settings, sdk: Mock) -> None:
    sdk.embeddings.create.return_value = response(1)
    source = chunks(1)
    results = embed_chunks(source, settings)
    assert results[0].chunk == source[0]
    assert results[0].vector == [1.0] * 3
    assert results[0].dimensions == 3
    assert results[0].deployment == "small-embedding"
    assert results[0].model == "text-embedding-3-small"
    sdk.embeddings.create.assert_called_once_with(
        model="small-embedding", input=[source[0].text], dimensions=3, encoding_format="float"
    )


def test_multiple_chunks_and_reordered_response(settings: Settings, sdk: Mock) -> None:
    data = response(2)
    data.data.reverse()
    sdk.embeddings.create.return_value = data
    source = chunks(2)
    results = embed_chunks(source, settings)
    assert [result.chunk for result in results] == source
    assert [result.vector for result in results] == [[1.0] * 3, [2.0] * 3]
    assert sdk.embeddings.create.call_count == 1


def test_batching_preserves_order_and_metadata(settings: Settings, sdk: Mock) -> None:
    sdk.embeddings.create.side_effect = [response(2), response(2), response(1)]
    source = chunks(5)
    results = embed_chunks(source, settings)
    assert [result.chunk.model_dump() for result in results] == [chunk.model_dump() for chunk in source]
    assert [call.kwargs["input"] for call in sdk.embeddings.create.call_args_list] == [
        [source[0].text, source[1].text], [source[2].text, source[3].text], [source[4].text],
    ]


@pytest.mark.parametrize("count", [0, 2])
def test_mismatched_response_counts(settings: Settings, sdk: Mock, count: int) -> None:
    sdk.embeddings.create.return_value = response(count)
    with pytest.raises(EmbeddingResponseError, match="count"):
        embed_chunks(chunks(1), settings)


@pytest.mark.parametrize("indices", [[0, 0], [0, 2], [-1, 0]])
def test_invalid_indices(settings: Settings, sdk: Mock, indices: list[int]) -> None:
    data = response(2)
    for item, index in zip(data.data, indices):
        item.index = index
    sdk.embeddings.create.return_value = data
    with pytest.raises(EmbeddingResponseError, match="indices"):
        embed_chunks(chunks(2), settings)


def test_wrong_dimensions(settings: Settings, sdk: Mock) -> None:
    sdk.embeddings.create.return_value = response(1, dimensions=2)
    with pytest.raises(EmbeddingResponseError, match="dimensions"):
        embed_chunks(chunks(1), settings)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad", True])
def test_invalid_vector_values(settings: Settings, sdk: Mock, value: object) -> None:
    data = response(1)
    data.data[0].embedding[0] = value
    sdk.embeddings.create.return_value = data
    with pytest.raises(EmbeddingResponseError, match="numeric"):
        embed_chunks(chunks(1), settings)


def test_wrong_model(settings: Settings, sdk: Mock) -> None:
    data = response(1)
    data.model = "text-embedding-3-large"
    sdk.embeddings.create.return_value = data
    with pytest.raises(EmbeddingResponseError, match="text-embedding-3-small"):
        embed_chunks(chunks(1), settings)


@pytest.mark.parametrize("kind", ["connection", "authentication", "rate_limit"])
def test_api_errors_do_not_expose_inputs(settings: Settings, sdk: Mock, kind: str) -> None:
    request = httpx.Request("POST", "https://example.openai.azure.com/openai/v1/embeddings")
    errors = {
        "connection": APIConnectionError(message="private document text", request=request),
        "authentication": ClientAuthenticationError("private document text"),
        "rate_limit": RateLimitError("private document text", response=httpx.Response(429, request=request), body=None),
    }
    sdk.embeddings.create.side_effect = errors[kind]
    with pytest.raises(EmbeddingAPIError) as error:
        embed_chunks(chunks(1), settings)
    assert "private document text" not in str(error.value)
    assert error.value.__cause__ is errors[kind]


def test_later_batch_failure_returns_no_partial_result(settings: Settings, sdk: Mock) -> None:
    sdk.embeddings.create.side_effect = [response(2), ClientAuthenticationError("failure")]
    with pytest.raises(EmbeddingAPIError):
        embed_chunks(chunks(5), settings)
    assert sdk.embeddings.create.call_count == 2


def test_missing_configuration(sdk: Mock) -> None:
    with pytest.raises(EmbeddingConfigurationError, match="AZURE_EMBEDDING_ENDPOINT"):
        embed_chunks(chunks(1), Settings(_env_file=None, AZURE_EMBEDDING_ENDPOINT="", AZURE_EMBEDDING_DEPLOYMENT=""))
    sdk.embeddings.create.assert_not_called()


@pytest.mark.parametrize("values", [
    {"AZURE_EMBEDDING_ENDPOINT": "http://example/openai/v1/"},
    {"AZURE_EMBEDDING_ENDPOINT": "https://example/api/projects/project"},
    {"AZURE_EMBEDDING_ENDPOINT": "https://example/openai/v1/?sig=secret"},
    {"AZURE_EMBEDDING_DEPLOYMENT": "name with spaces"},
    {"EMBEDDING_DIMENSIONS": 0}, {"EMBEDDING_DIMENSIONS": 1537},
    {"EMBEDDING_BATCH_SIZE": 0}, {"EMBEDDING_BATCH_SIZE": 33},
])
def test_invalid_configuration(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("text", ["  ", " word" * 8193], ids=["blank", "too-many-tokens"])
def test_invalid_input_rejected_before_api(settings: Settings, sdk: Mock, text: str) -> None:
    source = chunks(2)
    source[1].text = text
    with pytest.raises(ValueError, match="8192"):
        embed_chunks(source, settings)
    sdk.embeddings.create.assert_not_called()


def test_empty_input_does_not_call_api(settings: Settings, sdk: Mock) -> None:
    assert embed_chunks([], settings) == []
    sdk.embeddings.create.assert_not_called()


def test_keyless_client_configuration_and_cleanup(settings: Settings) -> None:
    with patch("app.integrations.embeddings.DefaultAzureCredential") as credentials, patch(
        "app.integrations.embeddings.get_bearer_token_provider"
    ) as token_provider, patch("app.integrations.embeddings.OpenAI") as factory:
        factory.return_value.__enter__.return_value.embeddings.create.return_value = response(1)
        AzureEmbeddings(settings).embed(["short text"])
        token_provider.assert_called_once_with(
            credentials.return_value.__enter__.return_value, "https://ai.azure.com/.default"
        )
        factory.assert_called_once_with(
            base_url=settings.azure_embedding_endpoint, api_key=token_provider.return_value,
            timeout=30.0, max_retries=2,
        )
        credentials.return_value.__exit__.assert_called_once()
        factory.return_value.__exit__.assert_called_once()
