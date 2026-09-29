"""Azure AI Search index lifecycle and validated batched chunk writes."""

import json
import math
from itertools import islice
from collections.abc import Sequence

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from pydantic import ValidationError
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration, HnswParameters, SearchField, SearchFieldDataType,
    SearchIndex, SearchableField, SimpleField, VectorSearch, VectorSearchProfile,
)

from app.config import Settings
from app.models import EmbeddedChunk, RetrievedChunk


class SearchError(RuntimeError):
    """Search configuration, schema, API, or per-document indexing failure."""


def build_index(name: str, dimensions: int) -> SearchIndex:
    if not 1 <= dimensions <= 1536:
        raise SearchError("Search dimensions must match the supported embedding dimensions (1–1536).")
    return SearchIndex(name=name, fields=[
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="filename", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="page_number", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32),
        SearchableField(name="text", type=SearchFieldDataType.String),
        SearchField(name="embedding", type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True, hidden=False, vector_search_dimensions=dimensions,
                    vector_search_profile_name="chunks-vector-profile"),
    ], vector_search=VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(
            name="chunks-hnsw", parameters=HnswParameters(
                metric="cosine", m=4, ef_construction=400, ef_search=500,
            ),
        )],
        profiles=[VectorSearchProfile(name="chunks-vector-profile", algorithm_configuration_name="chunks-hnsw")],
    ))


def validate_index(actual: SearchIndex, expected: SearchIndex) -> None:
    """Compare required semantics, ignoring service-populated defaults/ETags."""
    fields = {field.name: field for field in actual.fields}
    if set(fields) != {field.name for field in expected.fields}:
        raise SearchError("Existing Search index fields are incompatible; use a new index name or an explicit migration.")
    for wanted in expected.fields:
        field = fields[wanted.name]
        for attribute in ("type", "vector_search_dimensions", "vector_search_profile_name", "analyzer_name"):
            if getattr(field, attribute, None) != getattr(wanted, attribute, None):
                raise SearchError(f"Existing Search field {wanted.name} has incompatible {attribute}.")
        for attribute in ("key", "searchable", "filterable", "hidden"):
            if bool(getattr(field, attribute, False)) != bool(getattr(wanted, attribute, False)):
                raise SearchError(f"Existing Search field {wanted.name} has incompatible {attribute}.")
    vectors = actual.vector_search
    profiles = {profile.name: profile for profile in vectors.profiles or []} if vectors else {}
    algorithms = {algorithm.name: algorithm for algorithm in vectors.algorithms or []} if vectors else {}
    profile = profiles.get("chunks-vector-profile")
    algorithm = algorithms.get("chunks-hnsw")
    if (not profile or profile.algorithm_configuration_name != "chunks-hnsw"
            or profile.vectorizer_name or profile.compression_name
            or not algorithm or algorithm.kind != "hnsw"
            or not algorithm.parameters or algorithm.parameters.metric != "cosine"):
        raise SearchError("Existing Search vector configuration is incompatible.")


class SearchStore:
    def __init__(self, settings: Settings) -> None:
        self.endpoint = settings.azure_search_endpoint
        self.index_name = settings.azure_search_index_name
        self.dimensions = settings.embedding_dimensions

    def _require_endpoint(self) -> str:
        if not self.endpoint:
            raise SearchError("Set AZURE_SEARCH_ENDPOINT before using Azure Search.")
        return self.endpoint

    def ensure_index(self) -> None:
        expected = build_index(self.index_name, self.dimensions)
        endpoint = self._require_endpoint()
        try:
            with DefaultAzureCredential() as credential, SearchIndexClient(endpoint, credential) as client:
                try:
                    actual = client.get_index(self.index_name)
                except ResourceNotFoundError:
                    try:
                        actual = client.create_index(expected)
                    except ResourceExistsError:
                        # Another ingestion may have created it concurrently.
                        actual = client.get_index(self.index_name)
                validate_index(actual, expected)
        except AzureError as exc:
            raise SearchError("Search index validation/creation failed. Check configuration, RBAC, and connectivity.") from exc

    def index_chunks(self, chunks: Sequence[EmbeddedChunk], *, batch_size: int = 100) -> None:
        if not 1 <= batch_size <= 1000:
            raise SearchError("Search batch_size must be between 1 and 1000.")
        if not chunks:
            return
        endpoint = self._require_endpoint()
        documents = []
        keys: set[str] = set()
        for item in chunks:
            if (item.dimensions != self.dimensions or len(item.vector) != self.dimensions
                    or any(not math.isfinite(value) for value in item.vector)):
                raise SearchError("Chunk vector dimensions/values do not match Search configuration.")
            if item.chunk.chunk_id in keys:
                raise SearchError("Duplicate chunk IDs cannot be indexed in one call.")
            keys.add(item.chunk.chunk_id)
            documents.append({**item.chunk.model_dump(), "embedding": item.vector})
        # Validate byte sizes before sending any writes. Leave ample headroom
        # under Azure's 16 MB request limit for SDK action/envelope serialization.
        sizes = [len(json.dumps(doc, ensure_ascii=True).encode("utf-8")) + 128 for doc in documents]
        if any(size > 8_000_000 for size in sizes):
            raise SearchError("An individual Search document exceeds the supported payload size.")
        try:
            with DefaultAzureCredential() as credential, SearchClient(endpoint, self.index_name, credential) as client:
                batch, size = [], 0
                for document, document_size in zip(documents, sizes, strict=True):
                    if batch and (len(batch) >= batch_size or size + document_size > 8_000_000):
                        self._upload_batch(client, batch)
                        batch, size = [], 0
                    batch.append(document)
                    size += document_size
                if batch:
                    self._upload_batch(client, batch)
        except AzureError as exc:
            raise SearchError("Search indexing failed; some chunks may already exist. Check RBAC and connectivity.") from exc

    @staticmethod
    def _upload_batch(client: SearchClient, documents: list[dict]) -> None:
        results = client.upload_documents(documents=documents)
        expected = {document["chunk_id"] for document in documents}
        if (len(results) != len(documents) or {result.key for result in results} != expected
                or any(result.succeeded is not True for result in results)):
            raise SearchError("Search did not confirm every chunk; partial indexing may have occurred.")

    def inspect_chunk(self, chunk_id: str) -> dict:
        """Direct key lookup for manual indexing verification, not question retrieval."""
        endpoint = self._require_endpoint()
        try:
            with DefaultAzureCredential() as credential, SearchClient(endpoint, self.index_name, credential) as client:
                return client.get_document(key=chunk_id)
        except ResourceNotFoundError:
            raise
        except AzureError as exc:
            raise SearchError("Search document inspection failed.") from exc

    def hybrid_search(self, question: str, vector: list[float], top_k: int) -> list[RetrievedChunk]:
        endpoint = self._require_endpoint()
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        if len(vector) != self.dimensions or any(not math.isfinite(value) for value in vector):
            raise SearchError("Question vector does not match the configured embedding dimensions/values.")
        fields = ["document_id", "filename", "page_number", "chunk_id", "chunk_index", "text"]
        try:
            with DefaultAzureCredential() as credential, SearchClient(endpoint, self.index_name, credential) as client:
                results = client.search(
                    search_text=question, search_fields=["text"], query_type="simple",
                    vector_queries=[VectorizedQuery(vector=vector, fields="embedding", k_nearest_neighbors=50)],
                    select=fields, top=top_k,
                )
                # Consume lazy SDK results while the client is open; preserve RRF order.
                return [RetrievedChunk(
                    **{field: result[field] for field in fields},
                    search_score=result.get("@search.score"),
                ) for result in islice(results, top_k)]
        except AzureError as exc:
            raise SearchError("Azure Search retrieval failed. Check configuration, permissions, and connectivity.") from exc
        except (KeyError, ValidationError) as exc:
            raise SearchError("Azure Search returned invalid chunk metadata or scores.") from exc
