"""Retrieve evidence independently from any answer-generation logic."""

from app.config import Settings
from app.integrations.search import SearchStore
from app.models import RetrievalRequest, RetrievalResponse
from app.services.embeddings import embed_question


def retrieve_chunks(request: RetrievalRequest, settings: Settings, search_store: SearchStore) -> RetrievalResponse:
    vector = embed_question(request.question, settings)
    results = search_store.hybrid_search(
        request.question, vector, request.top_k if request.top_k is not None else settings.retrieval_top_k,
    )
    return RetrievalResponse(question=request.question, results=results)
