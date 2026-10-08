"""Grounded answer endpoint; retrieval remains independently available."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from app.access import DEMO_FILENAME

from app.config import Settings
from app.dependencies import get_search_store, get_settings
from app.integrations.embeddings import EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError
from app.integrations.generation import GenerationAPIError, GenerationConfigurationError, GenerationResponseError
from app.integrations.search import SearchError, SearchStore
from app.models import AnswerResponse, RetrievalRequest
from app.services.answers import answer_question

class DemoCitation(BaseModel):
    filename: str
    page_number: int

class DemoAnswerResponse(BaseModel):
    question: str
    status: Literal["supported", "insufficient_evidence"]
    answer: str
    citations: list[DemoCitation]

router = APIRouter(prefix="/api/answers", tags=["answers"])


@router.post("", response_model=AnswerResponse | DemoAnswerResponse)
async def answer(
    http_request: Request,
    request: RetrievalRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    search_store: Annotated[SearchStore, Depends(get_search_store)],
) -> AnswerResponse | DemoAnswerResponse:
    try:
        if settings.app_mode == "public_demo":
            response = await http_request.app.state.demo_runtime.execute(
                lambda: answer_question(request, settings, search_store))
            if any(c.document_id != str(settings.demo_document_id) or c.filename != DEMO_FILENAME
                   or not 1 <= c.page_number <= 6 for c in response.citations):
                raise GenerationResponseError("Answer citations escaped demo scope")
            return DemoAnswerResponse(question=response.question, status=response.status, answer=response.answer,
                citations=[DemoCitation(filename=c.filename, page_number=c.page_number) for c in response.citations])
        return await run_in_threadpool(answer_question, request, settings, search_store)
    except GenerationResponseError as exc:
        raise HTTPException(status_code=502, detail="The model returned an invalid grounded answer. Please try again.") from exc
    except (GenerationAPIError, GenerationConfigurationError, EmbeddingAPIError,
            EmbeddingConfigurationError, EmbeddingResponseError, SearchError) as exc:
        raise HTTPException(status_code=503, detail="Answer generation is unavailable. Check Azure configuration and services.") from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Question exceeds the supported embedding input limits.") from exc

    except Exception as exc:
        if settings.app_mode != "public_demo":
            raise
        raise HTTPException(status_code=503, detail="The demo is temporarily unavailable. Please try again later.") from exc
