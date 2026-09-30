"""Score observed results, without an LLM judge or assumed passing outcomes."""

import re
from typing import Any

from app.models import AnswerResponse, RetrievedChunk
from app.services.answers import INSUFFICIENT_EVIDENCE
from evaluation.dataset import Case


def page_keys(items: list[Any]) -> set[tuple[str, int]]:
    return {(item.filename, item.page_number) for item in items}


def score_case(
    case: Case, chunks: list[RetrievedChunk], supplied: list[RetrievedChunk],
    answer: AnswerResponse | None, *, mode: str, error_stage: str | None = None,
) -> dict[str, Any]:
    expected = page_keys(case.expected_sources)
    found = page_keys(chunks)
    available = page_keys(supplied)
    supported = case.expected_status == "supported"
    result: dict[str, Any] = {
        "hit_at_1": bool(expected & page_keys(chunks[:1])) if supported else None,
        "hit_at_3": bool(expected & page_keys(chunks[:3])) if supported else None,
        "expected_page_recall": len(expected & found) / len(expected) if supported else None,
        "all_expected_pages_retrieved": expected <= found if supported else None,
        "all_expected_pages_supplied": expected <= available if supported and mode == "answers" else None,
        "classification_correct": None,
        "citation_precision": None,
        "citation_page_recall": None,
        "citation_provenance_correct": None,
        "citations_correct": None,
        "answer_check_passed": None,
    }
    if mode == "answers":
        result["classification_correct"] = answer is not None and answer.status == case.expected_status
        cited = page_keys(answer.citations) if answer else set()
        metadata_fields = {"document_id", "filename", "page_number", "chunk_id", "chunk_index"}
        supplied_by_id = {f"S{index}": chunk for index, chunk in enumerate(supplied, 1)}
        provenance = answer is not None and all(
            citation.source_id in supplied_by_id
            and citation.model_dump(include=metadata_fields)
            == supplied_by_id[citation.source_id].model_dump(include=metadata_fields)
            for citation in answer.citations
        )
        result["citation_provenance_correct"] = provenance and (bool(cited) if supported else not cited)
        if supported:
            result["citation_precision"] = len(cited & expected) / len(cited) if cited else 0.0
            result["citation_page_recall"] = len(cited & expected) / len(expected)
            result["citations_correct"] = answer is not None and answer.status == "supported" and cited == expected and provenance
            if case.required_patterns:
                text = answer.answer if answer and answer.status == "supported" else ""
                result["answer_check_passed"] = bool(text) and all(
                    re.search(pattern, text, re.IGNORECASE) is not None for pattern in case.required_patterns
                ) and not any(re.search(pattern, text, re.IGNORECASE) for pattern in case.forbidden_patterns)
        else:
            abstained = (answer is not None and answer.status == "insufficient_evidence"
                         and answer.answer == INSUFFICIENT_EVIDENCE and not answer.citations)
            result["citations_correct"] = abstained
            result["answer_check_passed"] = abstained

    if error_stage:
        diagnosis = f"{error_stage}_error"
    elif supported and not result["all_expected_pages_retrieved"]:
        diagnosis = "retrieval_miss"
    elif supported and mode == "answers" and not result["all_expected_pages_supplied"]:
        diagnosis = "context_selection_miss"
    elif mode == "retrieval":
        diagnosis = "retrieval_hit" if supported else "unsupported_retrieval_not_scored"
    elif not all(result[key] for key in ("classification_correct", "citations_correct")) or result["answer_check_passed"] is False:
        diagnosis = "generation_check_failure" if supported else "abstention_failure"
    elif result["answer_check_passed"] is None:
        diagnosis = "manual_answer_review_required"
    else:
        diagnosis = "checks_passed"
    result["diagnosis"] = diagnosis
    return result


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for key in ("hit_at_1", "hit_at_3", "expected_page_recall", "all_expected_pages_retrieved",
                "all_expected_pages_supplied", "classification_correct", "citation_precision",
                "citation_page_recall", "citation_provenance_correct", "citations_correct", "answer_check_passed"):
        values = [row["scores"][key] for row in rows if row["scores"][key] is not None]
        metrics[key] = {"value": sum(values) / len(values) if values else None, "denominator": len(values)}
    # This subset isolates answer checks after all expected pages survived context selection.
    eligible = [row for row in rows if row["scores"]["all_expected_pages_supplied"] is True
                and row["scores"]["answer_check_passed"] is not None]
    values = [row["scores"]["answer_check_passed"] for row in eligible]
    metrics["answer_check_given_expected_evidence"] = {
        "value": sum(values) / len(values) if values else None, "denominator": len(values),
    }
    return {"cases": len(rows), "errors": sum(row["error"] is not None for row in rows), "metrics": metrics}
