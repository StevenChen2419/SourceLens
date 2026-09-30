"""Explicit live baseline: python -m evaluation.run --live --mode answers."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import Settings
from app.integrations.search import SearchStore
from app.models import RetrievalRequest, RetrievedChunk
from app.services.answers import answer_question, select_sources
from app.services.retrieval import retrieve_chunks
from evaluation.dataset import Case, load_dataset
from evaluation.scoring import score_case, summarize
from evaluation.controlled import evaluation_settings, verify_corpus

ROOT = Path(__file__).resolve().parent


class RecordingSearchStore(SearchStore):
    """Observe the real Search return without changing its query, order, or chunks."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.observed: list[RetrievedChunk] | None = None

    def hybrid_search(self, question: str, vector: list[float], top_k: int) -> list[RetrievedChunk]:
        chunks = super().hybrid_search(question, vector, top_k)
        self.observed = chunks
        return chunks


def evaluate_case(case: Case, settings: Settings, mode: str) -> dict[str, Any]:
    store = RecordingSearchStore(settings)
    request = RetrievalRequest(question=case.question)  # Preserve configured top_k.
    answer = None
    error = None
    stage = None
    supplied: dict[str, RetrievedChunk] = {}
    started = perf_counter()
    try:
        if mode == "retrieval":
            retrieve_chunks(request, settings, store)
        else:
            answer = answer_question(request, settings, store)
    except Exception as exc:
        # Batch boundary: preserve failures in results, never leak raw SDK errors.
        stage = "retrieval" if store.observed is None else "generation"
        error = {"stage": stage, "type": type(exc).__name__}
    chunks = store.observed or []
    if mode == "answers" and chunks:
        try:
            supplied = select_sources(chunks, settings.generation_context_tokens)
        except Exception as exc:
            stage = "context_selection"
            error = {"stage": stage, "type": type(exc).__name__}
    return {
        "id": case.id, "category": case.category, "section": case.section,
        "question": case.question, "criterion": case.criterion,
        "expected_status": case.expected_status,
        "expected_sources": [source.model_dump() for source in case.expected_sources],
        "required_patterns": case.required_patterns, "forbidden_patterns": case.forbidden_patterns,
        "retrieved": [chunk.model_dump() for chunk in chunks],
        "supplied_sources": {key: chunk.model_dump() for key, chunk in supplied.items()},
        "answer": answer.model_dump() if answer else None,
        "error": error, "elapsed_seconds": round(perf_counter() - started, 3),
        "scores": score_case(case, chunks, list(supplied.values()), answer, mode=mode, error_stage=stage),
    }


def code_hash() -> str:
    digest = hashlib.sha256()
    for directory in (ROOT.parent / "app", ROOT):
        for path in sorted(directory.rglob("*.py")):
            digest.update(path.relative_to(ROOT.parent).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def print_summary(summary: dict[str, Any]) -> None:
    print(f"\nCompleted {summary['cases']} cases; operational errors: {summary['errors']}")
    for name, metric in summary["metrics"].items():
        value = "N/A" if metric["value"] is None else f"{metric['value']:.1%}"
        print(f"  {name:32} {value:>6}  (n={metric['denominator']})")
    print("Answer checks are lexical proxies; review claims against passages. Page matches alone do not prove support.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly permit Azure calls")
    parser.add_argument("--mode", choices=["retrieval", "answers"], default="retrieval")
    parser.add_argument("--dataset", type=Path, default=ROOT / "handbook.json")
    parser.add_argument("--limit", type=int, help="Run only the first N cases as a smaller smoke check")
    parser.add_argument("--output", type=Path, help="New JSON artifact path; existing files are never overwritten")
    parser.add_argument("--index-name", help="Process-local controlled evaluation index override")
    parser.add_argument("--corpus-pdf", type=Path, help="Required with --index-name; verify the controlled corpus before evaluating")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("Azure evaluation requires --live; unit tests do not use this command")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if bool(args.index_name) != bool(args.corpus_pdf):
        parser.error("--index-name and --corpus-pdf must be supplied together")
    try:
        dataset = load_dataset(args.dataset)
        settings = Settings()
        if args.index_name:
            settings = evaluation_settings(settings, args.index_name)
    except (OSError, ValueError) as exc:
        print(f"Invalid dataset or settings ({type(exc).__name__}); check configuration before running.")
        return 2
    if settings.retrieval_top_k < 3:
        print("Hit@3 requires RETRIEVAL_TOP_K >= 3. No calls made; do not change an established baseline silently.")
        return 2
    cases = dataset.cases[:args.limit] if args.limit else dataset.cases
    timestamp = datetime.now(timezone.utc)
    output = args.output or ROOT / "results" / f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-{args.mode}.json"
    report: dict[str, Any] = {
        "format_version": 1, "started_at": timestamp.isoformat(), "complete": False,
        "dataset_version": dataset.version,
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "code_sha256": code_hash(), "mode": args.mode, "requested_cases": len(cases),
        "settings": settings.model_dump(include={
            "azure_search_index_name", "azure_embedding_deployment", "embedding_dimensions",
            "azure_generation_deployment", "retrieval_top_k", "generation_context_tokens",
            "generation_max_completion_tokens",
        }),
        "packages": {name: version(name) for name in ("openai", "azure-search-documents", "tiktoken")},
        "results": [],
    }
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2))
    except OSError:
        print("Cannot create output (it may already exist). No Azure calls made.")
        return 2
    count = len(cases)
    if args.index_name:
        try:
            report["controlled_corpus"] = verify_corpus(settings, args.corpus_pdf)
        except Exception as exc:
            report["preflight_error"] = type(exc).__name__
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Controlled corpus verification failed ({type(exc).__name__}); no evaluation questions sent. Run controlled verify for details.")
            return 2
    print(f"LIVE: {count} embedding + {count} Search calls, up to {count if args.mode == 'answers' else 0} GPT calls, plus retries.")
    print("Read-only evaluation; artifact contains answers and retrieved text. Keep it local.")
    for case in cases:
        row = evaluate_case(case, settings, args.mode)
        report["results"].append(row)
        report["summary"] = summarize(report["results"])
        # Preserve completed cases if a later request is interrupted.
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {case.id}: {row['scores']['diagnosis']}")
    report["complete"] = True
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print_summary(report["summary"])
    print(f"Results: {output.resolve()}")
    return 1 if report["summary"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
