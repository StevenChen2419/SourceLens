"""Azure OpenAI v1 embedding calls and validation of the provider response."""

import math
from collections.abc import Sequence

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import APIError, OpenAI

from app.config import Settings

EMBEDDING_MODEL = "text-embedding-3-small"


class EmbeddingConfigurationError(ValueError):
    """Embedding generation is not configured."""


class EmbeddingAPIError(RuntimeError):
    """Authentication or the remote embedding request failed."""


class EmbeddingResponseError(RuntimeError):
    """The provider returned unusable or incorrectly associated vectors."""


class AzureEmbeddings:
    def __init__(self, settings: Settings) -> None:
        if not settings.azure_embedding_endpoint or not settings.azure_embedding_deployment:
            raise EmbeddingConfigurationError(
                "Set AZURE_EMBEDDING_ENDPOINT and AZURE_EMBEDDING_DEPLOYMENT before generating embeddings."
            )
        self.endpoint = settings.azure_embedding_endpoint
        self.deployment = settings.azure_embedding_deployment
        self.dimensions = settings.embedding_dimensions
        self.timeout = settings.demo_azure_timeout_seconds if settings.app_mode == "public_demo" else 30.0
        self.max_retries = settings.demo_azure_max_retries if settings.app_mode == "public_demo" else 2

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed one already validated batch. Never log inputs or SDK error bodies."""
        if not texts:
            return []
        try:
            with DefaultAzureCredential() as credential:
                token_provider = get_bearer_token_provider(credential, "https://ai.azure.com/.default")
                with OpenAI(
                    base_url=self.endpoint, api_key=token_provider,
                    timeout=self.timeout, max_retries=self.max_retries,
                ) as client:
                    response = client.embeddings.create(
                        model=self.deployment, input=list(texts),
                        dimensions=self.dimensions, encoding_format="float",
                    )
        except (APIError, AzureError) as exc:
            raise EmbeddingAPIError(
                "Embedding request failed. Check Azure login, model permissions, deployment, quota, and connectivity."
            ) from exc

        if response.model != EMBEDDING_MODEL:
            raise EmbeddingResponseError("The deployment did not return text-embedding-3-small embeddings.")
        if len(response.data) != len(texts):
            raise EmbeddingResponseError("Embedding response count does not match the input count.")
        indices = [item.index for item in response.data]
        if any(type(index) is not int for index in indices) or sorted(indices) != list(range(len(texts))):
            raise EmbeddingResponseError("Embedding response indices are missing, duplicated, or out of range.")

        vectors = []
        for item in sorted(response.data, key=lambda item: item.index):
            vector = item.embedding
            if len(vector) != self.dimensions:
                raise EmbeddingResponseError("Embedding vector dimensions do not match EMBEDDING_DIMENSIONS.")
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                raise EmbeddingResponseError("Embedding vectors must contain finite numeric values.")
            vectors.append([float(value) for value in vector])
        return vectors
