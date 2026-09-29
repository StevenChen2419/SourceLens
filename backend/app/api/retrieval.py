"""HTTP endpoint for evidence retrieval only."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings
from app.dependencies import get_search_store, get_settings
from app.integrations.embeddings import EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError
from app.integrations.search import SearchError, SearchStore
from app.models import RetrievalRequest, RetrievalResponse
from app.services.retrieval import retrieve_chunks

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


@router.post("", response_model=RetrievalResponse)
def retrieve(
    request: RetrievalRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    search_store: Annotated[SearchStore, Depends(get_search_store)],
) -> RetrievalResponse:
    try:
        return retrieve_chunks(request, settings, search_store)
    except (EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError, SearchError) as exc:
        raise HTTPException(status_code=503, detail="Retrieval is unavailable. Check Azure configuration and services.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Question exceeds the supported embedding input limits.") from exc
