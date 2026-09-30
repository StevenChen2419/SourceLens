"""Guarded controlled-corpus setup, verification, and read-only diagnostics."""

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from app.config import Settings
from app.integrations.blob import BlobStore
from app.integrations.search import SearchStore
from app.models import DocumentUploadResponse, RetrievalRequest
from app.services.chunking import chunk_pages
from app.services.documents import upload_document
from app.services.pdf import extract_pdf_pages
from app.services.retrieval import retrieve_chunks
from evaluation.dataset import load_dataset

ROOT = Path(__file__).resolve().parent
RECEIPTS = ROOT / "results" / "corpora"
HANDBOOK_FILENAME = "knowledgeops-retrieval-test.pdf"
HANDBOOK_SHA256 = "6684b896fc83fd348e723d2ed3d7e67cf9dffa18b45721edb4aa0263de2ba4cf"


class ControlledCorpusError(ValueError):
    """A safety precondition or corpus verification failed."""


def evaluation_settings(settings: Settings, index_name: str) -> Settings:
    """Override only the index on a copy; never mutate application settings/env."""
    if (len(index_name) > 128 or not re.fullmatch(r"knowledgeops-eval-[a-z0-9]+(?:-[a-z0-9]+)*", index_name)
            or index_name in {"knowledgeops-chunks", settings.azure_search_index_name}):
        raise ControlledCorpusError("Use a distinct knowledgeops-eval-... index, never the configured application index.")
    return settings.model_copy(update={"azure_search_index_name": index_name})


def load_handbook(pdf: Path, settings: Settings) -> bytes:
    with pdf.open("rb") as stream:
        data = stream.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    if pdf.name != HANDBOOK_FILENAME or hashlib.sha256(data).hexdigest() != HANDBOOK_SHA256:
        raise ControlledCorpusError("PDF filename/hash does not match the Baseline v1 handbook. No ingestion performed.")
    return data


def corpus_identity(settings: Settings) -> dict:
    # Bind receipts to the service and embedding configuration without storing endpoints.
    service = hashlib.sha256((settings.azure_search_endpoint or "").encode()).hexdigest()
    embedding = hashlib.sha256(json.dumps([
        settings.azure_embedding_endpoint, settings.azure_embedding_deployment, settings.embedding_dimensions,
    ]).encode()).hexdigest()
    return {"service_sha256": service, "index_name": settings.azure_search_index_name,
            "embedding_configuration_sha256": embedding, "pdf_sha256": HANDBOOK_SHA256}


def receipt_path(settings: Settings) -> Path:
    identity = corpus_identity(settings)
    return RECEIPTS / f"{identity['service_sha256']}-{settings.azure_search_index_name}.json"


def ingest_once(settings: Settings, pdf: Path) -> dict:
    data = load_handbook(pdf, settings)
    store = SearchStore(settings)
    path = receipt_path(settings)
    if path.exists():
        raise ControlledCorpusError("An ingestion was already attempted. Use verify; do not retry ingestion or remove its receipt.")
    # Reuse schema creation/validation. No delete/recreate path exists.
    store.ensure_index()
    if store.inspect_documents(limit=1):
        raise ControlledCorpusError("Evaluation index is not empty. Choose a new evaluation index; nothing was deleted.")
    record = {**corpus_identity(settings), "state": "started", "started_at": datetime.now(timezone.utc).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2)
    except FileExistsError as exc:
        raise ControlledCorpusError("Another ingestion already reserved this index. Do not retry.") from exc
    try:
        result = upload_document(
            file=BytesIO(data), filename=HANDBOOK_FILENAME, content_type="application/pdf",
            max_bytes=settings.max_upload_size_mb * 1024 * 1024,
            blob_store=BlobStore(settings.azure_storage_account_url, settings.azure_storage_container),
            settings=settings, search_store=store,
        )
    except Exception as exc:
        record.update(state="failed", error_type=type(exc).__name__)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        raise ControlledCorpusError(
            "Ingestion failed; Blob/Search may contain partial data. Receipt retained; use a fresh evaluation index, not a retry."
        ) from exc
    record.update(state="ingested", upload=result.model_dump(mode="json"))
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def verify_corpus(settings: Settings, pdf: Path) -> dict:
    data = load_handbook(pdf, settings)
    record = json.loads(receipt_path(settings).read_text(encoding="utf-8"))
    if record.get("state") != "ingested" or any(record.get(k) != v for k, v in corpus_identity(settings).items()):
        raise ControlledCorpusError("Receipt is incomplete or belongs to different corpus/model settings.")
    upload = DocumentUploadResponse.model_validate(record["upload"])
    pages = extract_pdf_pages(data)
    chunks = chunk_pages(pages, document_id=str(upload.document_id), filename=HANDBOOK_FILENAME)
    if (upload.page_count != len(pages) or upload.chunk_count != len(chunks)
            or upload.filename != HANDBOOK_FILENAME or len(chunks) >= 1000):
        raise ControlledCorpusError("Receipt does not match the current PDF/chunking output.")
    # One extra result detects additional documents without approximate count APIs.
    actual = SearchStore(settings).inspect_documents(limit=len(chunks) + 1)
    expected = {chunk.chunk_id: chunk.model_dump() for chunk in chunks}
    if len(actual) != len(expected) or {row.get("chunk_id") for row in actual} != set(expected):
        raise ControlledCorpusError("Index has missing or extra chunks. If ingestion just finished, wait and rerun verify only.")
    for row in actual:
        if any(row.get(key) != value for key, value in expected[row["chunk_id"]].items()):
            raise ControlledCorpusError("Indexed text/metadata differs from the expected single ingestion.")
        vector = row.get("embedding")
        if (not isinstance(vector, list) or len(vector) != settings.embedding_dimensions
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector)):
            raise ControlledCorpusError("Indexed embedding dimensions/values are invalid.")
    return {**corpus_identity(settings), "document_id": str(upload.document_id),
            "filename": HANDBOOK_FILENAME, "page_count": len(pages), "chunk_count": len(chunks),
            "pages": sorted({chunk.page_number for chunk in chunks}), "verified_at": datetime.now(timezone.utc).isoformat()}


def diagnose(settings: Settings, top_k: int) -> None:
    case = next(case for case in load_dataset(ROOT / "handbook.json").cases if case.id == "handbook-18")
    response = retrieve_chunks(RetrievalRequest(question=case.question, top_k=top_k), settings, SearchStore(settings))
    print(case.question)
    print("Rank | Filename | Page | Search score")
    for rank, chunk in enumerate(response.results, 1):
        print(f"{rank} | {chunk.filename} | {chunk.page_number} | {chunk.search_score}")
    ranks = [i for i, chunk in enumerate(response.results, 1) if chunk.filename == HANDBOOK_FILENAME and chunk.page_number == 6]
    print(f"Expected page 6 ranks: {ranks or 'not returned'}; requested top {top_k}, returned {len(response.results)}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["ensure", "ingest", "verify", "diagnose"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--index-name", required=True)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--top-k", type=int, default=10, choices=range(1, 21))
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("Azure commands require --live")
    if args.action != "ensure" and args.pdf is None:
        parser.error("--pdf is required for ingestion and corpus verification")
    try:
        settings = evaluation_settings(Settings(), args.index_name)
        if args.action == "ensure":
            SearchStore(settings).ensure_index()
            print(f"Compatible evaluation index: {settings.azure_search_index_name}. No existing data replaced.")
        elif args.action == "ingest":
            print(json.dumps(ingest_once(settings, args.pdf), indent=2))
            print("Ingestion confirmed. Run verify after Search makes the chunks query-visible; do not ingest again.")
        else:
            print(json.dumps(verify_corpus(settings, args.pdf), indent=2))
            if args.action == "diagnose":
                diagnose(settings, args.top_k)
    except ControlledCorpusError as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        print(f"Controlled command failed ({type(exc).__name__}). Check local files, configuration, Azure access, and any receipt. No cleanup was attempted.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
