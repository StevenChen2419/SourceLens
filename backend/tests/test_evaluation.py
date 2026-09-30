import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.integrations.generation import GenerationAPIError, ModelAnswer
from app.integrations.search import SearchError, SearchStore
from app.models import AnswerResponse, Citation, RetrievedChunk
from app.services.answers import INSUFFICIENT_EVIDENCE
from evaluation.dataset import Case, Dataset, load_dataset
from evaluation.run import ROOT, evaluate_case, main
from evaluation.scoring import score_case, summarize


def example_case(**changes) -> Case:
    values = dict(id="test", category="direct", section="Leave", question="How many days?",
                  expected_status="supported", expected_sources=[{"filename": "handbook.pdf", "page_number": 1}],
                  criterion="15 days", required_patterns=[r"\b(15|fifteen)\b", r"\bdays\b"])
    return Case(**(values | changes))


def chunk(page: int = 1) -> RetrievedChunk:
    return RetrievedChunk(document_id="doc", filename="handbook.pdf", page_number=page,
                          chunk_id=f"chunk-{page}", chunk_index=page - 1, text="Employees receive 15 vacation days.")


def answer(text: str = "15 days", page: int = 1) -> AnswerResponse:
    return AnswerResponse(question="How many days?", status="supported", answer=text,
                          citations=[Citation(source_id="S1", **chunk(page).model_dump(exclude={"text", "search_score"}))])


def test_handbook_dataset_covers_all_sections_and_categories() -> None:
    dataset = load_dataset(ROOT / "handbook.json")
    assert len(dataset.cases) == 26
    supported = [case for case in dataset.cases if case.expected_status == "supported"]
    assert len(supported) == 18
    assert {source.page_number for case in supported for source in case.expected_sources} == set(range(1, 7))
    assert {case.category for case in supported} == {"direct", "paraphrase", "semantic"}
    assert all(case.required_patterns for case in supported)
    assert all(source.filename == "knowledgeops-retrieval-test.pdf" for case in supported for source in case.expected_sources)


@pytest.mark.parametrize("changes", [
    {"question": "  "}, {"expected_sources": []}, {"required_patterns": ["["]},
    {"category": "unsupported"}, {"unexpected": True},
    {"expected_sources": [{"filename": "a.pdf", "page_number": 0}]},
])
def test_invalid_cases_are_rejected(changes) -> None:
    with pytest.raises(ValidationError):
        example_case(**changes)


def test_duplicate_case_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Dataset(version="1", description="test", cases=[example_case(), example_case()])


def test_ranked_retrieval_is_scored_without_generation() -> None:
    scores = score_case(example_case(), [chunk(2), chunk(3), chunk()], [], None, mode="retrieval")
    assert scores["hit_at_1"] is False
    assert scores["hit_at_3"] is True
    assert scores["expected_page_recall"] == 1
    assert scores["classification_correct"] is None
    assert scores["answer_check_passed"] is None


def test_expected_page_at_rank_four_is_not_hit_at_three() -> None:
    scores = score_case(example_case(), [chunk(2), chunk(3), chunk(4), chunk()], [], None, mode="retrieval")
    assert scores["hit_at_3"] is False
    assert scores["all_expected_pages_retrieved"] is True


@pytest.mark.parametrize("retrieved,supplied,response,diagnosis", [
    ([], [], answer(), "retrieval_miss"),
    ([chunk()], [], answer(), "context_selection_miss"),
    ([chunk()], [chunk()], answer("20 days"), "generation_check_failure"),
    ([chunk()], [chunk()], answer("fifteen days"), "checks_passed"),
])
def test_failure_attribution(retrieved, supplied, response, diagnosis) -> None:
    assert score_case(example_case(), retrieved, supplied, response, mode="answers")["diagnosis"] == diagnosis


def test_citation_page_and_provenance_checks() -> None:
    response = answer()
    response.citations.append(Citation(source_id="S2", **chunk(2).model_dump(exclude={"text", "search_score"})))
    scores = score_case(example_case(), [chunk(), chunk(2)], [chunk(), chunk(2)], response, mode="answers")
    assert scores["citation_precision"] == 0.5
    assert scores["citation_page_recall"] == 1
    assert scores["citations_correct"] is False
    response = answer()
    response.citations[0].document_id = "invented"
    scores = score_case(example_case(), [chunk()], [chunk()], response, mode="answers")
    assert scores["citation_provenance_correct"] is False
    assert scores["citations_correct"] is False


def test_duplicate_citations_do_not_inflate_page_metrics() -> None:
    response = answer()
    response.citations *= 2
    scores = score_case(example_case(), [chunk()], [chunk()], response, mode="answers")
    assert scores["citation_precision"] == 1
    assert scores["citation_page_recall"] == 1


def test_multi_page_coverage_requires_every_expected_page() -> None:
    case = example_case(expected_sources=[{"filename": "handbook.pdf", "page_number": n} for n in (1, 2)])
    scores = score_case(case, [chunk()], [chunk()], answer(), mode="answers")
    assert scores["hit_at_1"] is True
    assert scores["expected_page_recall"] == 0.5
    assert scores["all_expected_pages_retrieved"] is False


def test_abstention_requires_canonical_message_and_no_citations() -> None:
    case = example_case(category="unsupported", expected_status="insufficient_evidence", expected_sources=[], required_patterns=[])
    response = AnswerResponse(question=case.question, status="insufficient_evidence", answer=INSUFFICIENT_EVIDENCE, citations=[])
    scores = score_case(case, [chunk()], [chunk()], response, mode="answers")
    assert scores["hit_at_1"] is None
    assert scores["answer_check_passed"] is True
    response.answer = "Tokyo"
    assert score_case(case, [], [], response, mode="answers")["answer_check_passed"] is False
    response.answer = INSUFFICIENT_EVIDENCE
    response.citations = answer().citations
    assert score_case(case, [], [], response, mode="answers")["answer_check_passed"] is False


def test_optional_checks_are_not_assumed_to_pass_and_forbidden_patterns_fail() -> None:
    scores = score_case(example_case(required_patterns=[]), [chunk()], [chunk()], answer(), mode="answers")
    assert scores["answer_check_passed"] is None
    assert scores["diagnosis"] == "manual_answer_review_required"
    scores = score_case(example_case(forbidden_patterns=[r"\b20\b"]), [chunk()], [chunk()], answer("15 days or 20 days"), mode="answers")
    assert scores["answer_check_passed"] is False


def test_summary_uses_actual_values_denominators_and_keeps_failures() -> None:
    passed = score_case(example_case(), [chunk()], [chunk()], answer(), mode="answers")
    failed = score_case(example_case(), [], [], None, mode="answers", error_stage="retrieval")
    summary = summarize([{"scores": passed, "error": None}, {"scores": failed, "error": {"stage": "retrieval"}}])
    assert summary["metrics"]["hit_at_1"] == {"value": 0.5, "denominator": 2}
    assert summary["metrics"]["answer_check_passed"]["value"] == 0.5
    assert summary["metrics"]["answer_check_given_expected_evidence"] == {"value": 1, "denominator": 1}
    assert summary["errors"] == 1
    assert summarize([])["metrics"]["hit_at_1"] == {"value": None, "denominator": 0}


@pytest.fixture
def offline_clients():
    with patch("app.services.retrieval.embed_question", return_value=[0.1] * 1536) as embedding, \
         patch.object(SearchStore, "hybrid_search", return_value=[chunk()]) as search, \
         patch("app.services.answers.AzureAnswerGenerator") as generator:
        generator.return_value.generate.return_value = ModelAnswer(status="supported", answer="15 days", source_ids=["S1"])
        yield embedding, search, generator


def test_runner_exercises_real_services_once_and_captures_actual_evidence(offline_clients) -> None:
    embedding, search, generator = offline_clients
    row = evaluate_case(example_case(), Settings(_env_file=None), "answers")
    assert row["scores"]["diagnosis"] == "checks_passed"
    assert row["supplied_sources"]["S1"]["page_number"] == 1
    embedding.assert_called_once()
    search.assert_called_once()
    generator.return_value.generate.assert_called_once_with("How many days?", {"S1": chunk().text})


def test_retrieval_mode_never_generates(offline_clients) -> None:
    row = evaluate_case(example_case(), Settings(_env_file=None), "retrieval")
    assert row["scores"]["hit_at_1"] is True
    offline_clients[2].assert_not_called()


@pytest.mark.parametrize("stage", ["retrieval", "generation"])
def test_runner_records_api_failure_without_leaking_exception_text(offline_clients, stage) -> None:
    if stage == "retrieval":
        offline_clients[1].side_effect = SearchError("private credential detail")
    else:
        offline_clients[2].return_value.generate.side_effect = GenerationAPIError("private credential detail")
    row = evaluate_case(example_case(), Settings(_env_file=None), "answers")
    assert row["error"]["stage"] == stage
    assert "private credential detail" not in json.dumps(row)
    assert row["scores"]["classification_correct"] is False


def test_live_flag_required_before_settings_or_service_calls() -> None:
    with patch("evaluation.run.Settings") as settings, pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
    settings.assert_not_called()


def test_cli_writes_actual_artifact_and_refuses_overwrite(tmp_path: Path, offline_clients) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text(Dataset(version="test", description="test", cases=[example_case()]).model_dump_json())
    output = tmp_path / "results.json"
    args = ["--live", "--mode", "answers", "--dataset", str(dataset), "--output", str(output)]
    assert main(args) == 0
    report = json.loads(output.read_text())
    assert report["complete"] is True
    assert report["summary"]["metrics"]["hit_at_1"]["value"] == 1
    assert len(report["dataset_sha256"]) == 64
    assert "azure_generation_endpoint" not in report["settings"]
    assert main(args) == 2
    offline_clients[0].assert_called_once()
