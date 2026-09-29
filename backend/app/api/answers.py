"""Grounded answer endpoint; retrieval remains independently available."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings
from app.dependencies import get_search_store, get_settings
from app.integrations.embeddings import EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError
from app.integrations.generation import GenerationAPIError, GenerationConfigurationError, GenerationResponseError
from app.integrations.search import SearchError, SearchStore
from app.models import AnswerResponse, RetrievalRequest
from app.services.answers import answer_question

router = APIRouter(prefix="/api/answers", tags=["answers"])


@router.post("", response_model=AnswerResponse)
def answer(
    request: RetrievalRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    search_store: Annotated[SearchStore, Depends(get_search_store)],
) -> AnswerResponse:
    try:
        return answer_question(request, settings, search_store)
    except GenerationResponseError as exc:
        raise HTTPException(status_code=502, detail="The model returned an invalid grounded answer. Please try again.") from exc
    except (GenerationAPIError, GenerationConfigurationError, EmbeddingAPIError,
            EmbeddingConfigurationError, EmbeddingResponseError, SearchError) as exc:
        raise HTTPException(status_code=503, detail="Answer generation is unavailable. Check Azure configuration and services.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Question exceeds the supported embedding input limits.") from exc
