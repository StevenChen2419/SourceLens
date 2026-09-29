"""Reuse retrieval, bound evidence, and build citations from trusted metadata."""

import json

import tiktoken

from app.config import Settings
from app.integrations.generation import AzureAnswerGenerator, GenerationResponseError
from app.integrations.search import SearchStore
from app.models import AnswerResponse, Citation, RetrievalRequest, RetrievedChunk
from app.services.retrieval import retrieve_chunks

INSUFFICIENT_EVIDENCE = "The retrieved documents do not contain enough evidence to answer this question."


def select_sources(chunks: list[RetrievedChunk], token_budget: int) -> dict[str, RetrievedChunk]:
    """Keep complete passages in retrieval order, deduplicated by document/chunk."""
    encoding = tiktoken.get_encoding("o200k_base")
    sources: dict[str, RetrievedChunk] = {}
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        key = (chunk.document_id, chunk.chunk_id)
        if key in seen or not chunk.text.strip():
            continue
        source_id = f"S{len(sources) + 1}"
        candidate = {source: item.text for source, item in sources.items()}
        candidate[source_id] = chunk.text
        if len(encoding.encode_ordinary(json.dumps(candidate, ensure_ascii=False))) > token_budget:
            continue
        sources[source_id] = chunk
        seen.add(key)
    return sources


def answer_question(request: RetrievalRequest, settings: Settings, search_store: SearchStore) -> AnswerResponse:
    retrieved = retrieve_chunks(request, settings, search_store)
    sources = select_sources(retrieved.results, settings.generation_context_tokens) if retrieved.results else {}
    if not sources:
        return AnswerResponse(question=request.question, status="insufficient_evidence",
                              answer=INSUFFICIENT_EVIDENCE, citations=[])
    output = AzureAnswerGenerator(settings).generate(request.question, {key: chunk.text for key, chunk in sources.items()})
    if any(source_id not in sources for source_id in output.source_ids):
        raise GenerationResponseError("Generation referenced an unknown source ID.")
    if output.status == "insufficient_evidence":
        # Never expose model text on an abstention, even if it includes an answer.
        return AnswerResponse(question=request.question, status="insufficient_evidence",
                              answer=INSUFFICIENT_EVIDENCE, citations=[])
    if not output.answer.strip() or not output.source_ids:
        raise GenerationResponseError("A supported answer must include answer text and valid source IDs.")
    citations = []
    for source_id in dict.fromkeys(output.source_ids):
        chunk = sources[source_id]
        citations.append(Citation(source_id=source_id, **chunk.model_dump(
            include={"document_id", "filename", "page_number", "chunk_id", "chunk_index"},
        )))
    return AnswerResponse(question=request.question, status="supported", answer=output.answer.strip(), citations=citations)
