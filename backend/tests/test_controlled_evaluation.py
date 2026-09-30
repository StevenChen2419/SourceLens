import hashlib
import json
from unittest.mock import Mock, patch

import pytest
from azure.core.exceptions import HttpResponseError

from app.config import Settings
from app.integrations.search import SearchError, SearchStore
from app.models import EmbeddedChunk, RetrievalResponse
from evaluation import controlled, run


@pytest.fixture
def settings():
    return Settings(_env_file=None, AZURE_SEARCH_ENDPOINT="https://example.search.windows.net")


def test_index_override_changes_only_index_and_leaves_original_intact(settings):
    before = settings.model_dump()
    selected = controlled.evaluation_settings(settings, "knowledgeops-eval-chunks")
    assert settings.model_dump() == before
    assert selected.model_dump() == before | {"azure_search_index_name": "knowledgeops-eval-chunks"}


@pytest.mark.parametrize("name", ["knowledgeops-chunks", "other-index", "knowledgeops-eval-", "knowledgeops-eval-../x", "KNOWLEDGEOPS-eval-test"])
def test_protected_or_invalid_names_rejected(settings, name):
    with pytest.raises(controlled.ControlledCorpusError):
        controlled.evaluation_settings(settings, name)


def test_configured_application_index_also_protected(settings):
    settings.azure_search_index_name = "knowledgeops-eval-app"
    with pytest.raises(controlled.ControlledCorpusError):
        controlled.evaluation_settings(settings, "knowledgeops-eval-app")


def test_missing_live_flag_never_creates_clients():
    with patch.object(controlled, "SearchStore") as store, pytest.raises(SystemExit):
        controlled.main(["ensure", "--index-name", "knowledgeops-eval-chunks"])
    store.assert_not_called()


def test_ensure_uses_existing_schema_entrypoint_and_protected_index_is_never_touched(settings):
    with patch.object(controlled, "Settings", return_value=settings), patch.object(controlled, "SearchStore") as store:
        assert controlled.main(["ensure", "--live", "--index-name", "knowledgeops-chunks"]) == 1
        store.assert_not_called()
        assert controlled.main(["ensure", "--live", "--index-name", "knowledgeops-eval-chunks"]) == 0
        assert store.call_args.args[0].azure_search_index_name == "knowledgeops-eval-chunks"
        store.return_value.ensure_index.assert_called_once()


@pytest.fixture
def corpus(tmp_path, monkeypatch, make_pdf, settings):
    pdf = tmp_path / controlled.HANDBOOK_FILENAME
    data = make_pdf(["Vacation policy", "Remote work", "IT support", "Expenses", "Benefits", "Security"])
    pdf.write_bytes(data)
    monkeypatch.setattr(controlled, "HANDBOOK_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(controlled, "RECEIPTS", tmp_path / "receipts")
    selected = controlled.evaluation_settings(settings, "knowledgeops-eval-chunks")
    documents = []

    def embed(chunks, config):
        assert config.azure_search_index_name == "knowledgeops-eval-chunks"
        return [EmbeddedChunk(chunk=chunk, vector=[0.1] * config.embedding_dimensions,
                              model="text-embedding-3-small", deployment="embedding", dimensions=config.embedding_dimensions)
                for chunk in chunks]

    def index(store, chunks):
        assert store.index_name == "knowledgeops-eval-chunks"
        documents.extend({**item.chunk.model_dump(), "embedding": item.vector} for item in chunks)

    with patch.object(SearchStore, "ensure_index") as ensure, \
         patch.object(SearchStore, "inspect_documents", side_effect=lambda *, limit: documents[:limit]), \
         patch.object(SearchStore, "index_chunks", autospec=True, side_effect=index), \
         patch("app.services.documents.embed_chunks", side_effect=embed) as embedding, \
         patch.object(controlled, "BlobStore") as blob:
        yield selected, pdf, documents, ensure, embedding, blob


def test_reuses_real_ingestion_once_and_verifies_exact_corpus(corpus):
    settings, pdf, documents, ensure, embedding, blob = corpus
    record = controlled.ingest_once(settings, pdf)
    assert record["upload"]["chunk_count"] == 6
    assert len({row["document_id"] for row in documents}) == 1
    assert controlled.verify_corpus(settings, pdf)["pages"] == [1, 2, 3, 4, 5, 6]
    embedding.assert_called_once()
    blob.return_value.upload_pdf.assert_called_once()
    # Simulate Search visibility lag: the receipt still prevents another ingestion.
    documents.clear()
    with pytest.raises(controlled.ControlledCorpusError, match="already attempted"):
        controlled.ingest_once(settings, pdf)
    embedding.assert_called_once()


def test_nonempty_index_refuses_any_ingestion(corpus):
    settings, pdf, documents, _, embedding, blob = corpus
    documents.append({"chunk_id": "preexisting"})
    with pytest.raises(controlled.ControlledCorpusError, match="not empty"):
        controlled.ingest_once(settings, pdf)
    embedding.assert_not_called()
    blob.assert_not_called()
    assert not controlled.receipt_path(settings).exists()


def test_wrong_pdf_is_rejected_before_azure(corpus):
    settings, pdf, _, ensure, embedding, _ = corpus
    pdf.write_bytes(b"different PDF")
    with pytest.raises(controlled.ControlledCorpusError, match="hash"):
        controlled.ingest_once(settings, pdf)
    ensure.assert_not_called()
    embedding.assert_not_called()


def test_failed_attempt_retains_receipt_and_blocks_retries(corpus):
    settings, pdf, _, _, embedding, _ = corpus
    embedding.side_effect = RuntimeError("private error details")
    with pytest.raises(controlled.ControlledCorpusError, match="partial data"):
        controlled.ingest_once(settings, pdf)
    record = json.loads(controlled.receipt_path(settings).read_text())
    assert record["state"] == "failed"
    assert "private error details" not in str(record)
    with pytest.raises(controlled.ControlledCorpusError, match="already attempted"):
        controlled.ingest_once(settings, pdf)
    embedding.assert_called_once()


@pytest.mark.parametrize("damage", ["missing", "extra", "document", "text", "vector", "nan"])
def test_verify_detects_incomplete_foreign_or_changed_chunks(corpus, damage):
    settings, pdf, documents, *_ = corpus
    controlled.ingest_once(settings, pdf)
    if damage == "missing":
        documents.pop()
    elif damage == "extra":
        documents.append({**documents[0], "chunk_id": "extra"})
    elif damage == "document":
        documents[0]["document_id"] = "another-upload"
    elif damage == "text":
        documents[0]["text"] = "changed"
    elif damage == "vector":
        documents[0]["embedding"] = [0.1]
    else:
        documents[0]["embedding"][0] = float("nan")
    with pytest.raises(controlled.ControlledCorpusError):
        controlled.verify_corpus(settings, pdf)


def test_receipt_cannot_be_reused_for_another_model(corpus):
    settings, pdf, *_ = corpus
    controlled.ingest_once(settings, pdf)
    changed = settings.model_copy(update={"azure_embedding_deployment": "different"})
    with pytest.raises(controlled.ControlledCorpusError, match="different"):
        controlled.verify_corpus(changed, pdf)


def test_inspection_uses_only_selected_index_and_returns_bounded_rows(settings):
    selected = controlled.evaluation_settings(settings, "knowledgeops-eval-chunks")
    with patch("app.integrations.search.DefaultAzureCredential"), patch("app.integrations.search.SearchClient") as factory:
        client = factory.return_value.__enter__.return_value
        client.search.return_value = iter([{"chunk_id": str(i)} for i in range(10)])
        assert len(SearchStore(selected).inspect_documents(limit=7)) == 7
        assert factory.call_args.args[1] == "knowledgeops-eval-chunks"
        assert client.search.call_args.kwargs["search_text"] == "*"
        assert client.search.call_args.kwargs["top"] == 7
        client.upload_documents.assert_not_called()
        client.delete_documents.assert_not_called()
        client.search.side_effect = HttpResponseError("unavailable")
        with pytest.raises(SearchError):
            SearchStore(selected).inspect_documents(limit=1)


def test_diagnostic_top_k_is_request_local_and_uses_dataset_question(settings, capsys):
    selected = controlled.evaluation_settings(settings, "knowledgeops-eval-chunks")
    with patch.object(controlled, "retrieve_chunks", return_value=RetrievalResponse(question="test", results=[])) as retrieve:
        controlled.diagnose(selected, 10)
    request, used_settings, store = retrieve.call_args.args
    assert request.top_k == 10
    assert request.question.startswith("My work laptop disappeared")
    assert settings.retrieval_top_k == selected.retrieval_top_k == 5
    assert store.index_name == used_settings.azure_search_index_name == "knowledgeops-eval-chunks"
    assert "not returned" in capsys.readouterr().out


def test_runner_selects_controlled_index_and_records_verification(tmp_path, settings):
    output = tmp_path / "controlled.json"
    args = ["--live", "--mode", "answers", "--limit", "1", "--index-name", "knowledgeops-eval-chunks",
            "--corpus-pdf", "test.pdf", "--output", str(output)]
    with patch.object(run, "Settings", return_value=settings), \
         patch.object(run, "verify_corpus", return_value={"chunk_count": 6}) as verify, \
         patch.object(run, "evaluate_case", return_value={"id": "test", "scores": {"diagnosis": "checks_passed"}}) as evaluate, \
         patch.object(run, "summarize", return_value={"cases": 1, "errors": 0, "metrics": {}}):
        assert run.main(args) == 0
    assert evaluate.call_args.args[1].azure_search_index_name == "knowledgeops-eval-chunks"
    assert verify.call_args.args[0].azure_search_index_name == "knowledgeops-eval-chunks"
    assert settings.azure_search_index_name == "knowledgeops-chunks"
    report = json.loads(output.read_text())
    assert report["controlled_corpus"]["chunk_count"] == 6
    assert report["settings"]["retrieval_top_k"] == 5
    with patch.object(run, "Settings", return_value=settings), patch.object(run, "verify_corpus") as verify:
        assert run.main(args) == 2  # Existing artifact is never overwritten.
        verify.assert_not_called()


def test_runner_failed_corpus_preflight_never_evaluates(tmp_path, settings):
    with patch.object(run, "Settings", return_value=settings), \
         patch.object(run, "verify_corpus", side_effect=controlled.ControlledCorpusError("extra chunks")), \
         patch.object(run, "evaluate_case") as evaluate:
        assert run.main(["--live", "--index-name", "knowledgeops-eval-chunks", "--corpus-pdf", "test.pdf",
                         "--output", str(tmp_path / "failed.json")]) == 2
        evaluate.assert_not_called()
