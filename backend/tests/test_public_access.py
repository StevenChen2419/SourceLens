"""Public-demo security regressions. All Azure clients are mocked."""
import asyncio
import json
import threading
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.access import DemoRuntime, DemoSearchStore, SUGGESTED_QUESTIONS
from app.config import Settings
from app.main import create_app
from app.models import RetrievedChunk
from app.integrations.generation import ModelAnswer
from app.integrations.search import SearchError, SearchStore
from test_production import production_values

DOCUMENT_ID = "00000000-0000-4000-8000-000000000001"

def settings(**overrides):
    values = dict(APP_MODE="public_demo", DEMO_DOCUMENT_ID=DOCUMENT_ID,
                  AZURE_SEARCH_ENDPOINT="https://search.example.com")
    values.update(overrides)
    return Settings(_env_file=None, **values)


def rows():
    return [dict(document_id=DOCUMENT_ID, filename="employee-handbook.pdf", page_number=p,
                 chunk_index=p-1, chunk_id=f"chunk-{p}") for p in range(1, 7)]


def evidence():
    return RetrievedChunk(**rows()[0], text="15 vacation days", search_score=0.03)


@pytest.mark.parametrize("mode", ["authenticated", "production", "", "PUBLIC_DEMO", "invalid"])
def test_invalid_modes(mode):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_MODE=mode)


def test_default_is_local_development_and_explicit_demo_has_no_management():
    local = create_app(Settings(_env_file=None))
    with TestClient(local) as client:
        assert client.get("/api/config").json() == dict(mode="development", can_manage_documents=True,
            max_question_chars=4000, demo_filename=None, suggested_questions=[])
        assert client.get("/openapi.json").status_code == 200
    with TestClient(create_app(settings())) as client:
        config = client.get("/api/config").json()
        assert config["mode"] == "public_demo" and not config["can_manage_documents"]
        assert config["suggested_questions"] == SUGGESTED_QUESTIONS
        assert "document_id" not in json.dumps(config)
        assert "search.example" not in json.dumps(config)
        assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("mode", [None, "development"])
def test_production_fails_closed_without_explicit_demo(mode):
    values = production_values()
    values["APP_MODE"] = mode
    with pytest.raises(ValidationError, match="explicit APP_MODE"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("overrides", [
    {"DEMO_DOCUMENT_ID": None}, {"DEMO_DOCUMENT_ID": "bad-uuid"},
    {"AZURE_SEARCH_ENDPOINT": None}, {"AZURE_SEARCH_INDEX_NAME": "knowledgeops-eval-chunks"},
    {"DEMO_TOP_K": 6}, {"DEMO_MAX_CONCURRENCY": 3}, {"DEMO_AZURE_MAX_RETRIES": 2},
    {"DEMO_MAX_QUESTION_CHARS": 1001}, {"DEMO_MAX_BODY_BYTES": 16385},
])
def test_demo_configuration_validation(overrides):
    with pytest.raises(ValidationError):
        settings(**overrides)


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/documents"), ("GET", "/api/documents"),
    ("DELETE", f"/api/documents/{DOCUMENT_ID}"), ("POST", "/api/retrieval"),
    ("GET", "/docs"), ("GET", "/redoc"), ("GET", "/openapi.json"),
    ("GET", "/api/answers"), ("POST", "/api/answers/"), ("GET", "/unknown"),
    ("HEAD", "/health"), ("OPTIONS", "/api/documents"),
])
def test_strict_allowlist_blocks_handlers_and_unknown_routes(method, path):
    application = create_app(settings())
    handler = Mock(return_value={"secret": "never exposed"})
    application.get("/unknown")(handler)
    with patch("app.integrations.search.DefaultAzureCredential") as credentials, TestClient(application) as client:
        response = client.request(method, path, follow_redirects=False,
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
        assert response.status_code == 403
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        handler.assert_not_called()
        credentials.assert_not_called()


def test_permitted_preflight_and_denied_origin():
    with TestClient(create_app(settings())) as client:
        for origin, code in [("http://localhost:5173", 200), ("https://evil.example", 400)]:
            assert client.options("/api/answers", headers={"Origin": origin,
                "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"}).status_code == code


@pytest.mark.parametrize("body", [
    {"question": "policy", "document_id": DOCUMENT_ID}, {"question": "policy", "index_name": "other"},
    {"question": "policy", "top_k": 20}, {"question": "policy", "filter": "*"},
    {"question": "policy", "azure_search_index_name": "other"}, {"question": " "},
    {"question": "x" * 501}, {"question": None}, {"question": 123},
])
def test_public_request_rejects_selectors_and_invalid_questions(body):
    with patch("app.api.answers.answer_question") as answer, TestClient(create_app(settings())) as client:
        assert client.post("/api/answers", json=body).status_code == 422
        answer.assert_not_called()


@pytest.mark.parametrize("path", ["/api/answers?document_id=other", "/api/answers?index_name=other", "/api/config?mode=development"])
def test_no_query_selectors(path):
    with TestClient(create_app(settings())) as client:
        method = client.get if path.startswith("/api/config") else client.post
        assert method(path).status_code == 422


def test_body_limit_content_type_and_duplicate_keys():
    with patch("app.api.answers.answer_question") as answer, TestClient(create_app(settings())) as client:
        assert client.post("/api/answers", content=b"x" * 8193, headers={"Content-Type": "application/json"}).status_code == 413
        assert client.post("/api/answers", content="plain").status_code == 415
        assert client.post("/api/answers", content='{"question":"a","question":"b"}', headers={"Content-Type": "application/json"}).status_code == 422
        assert client.post("/api/answers", content="invalid", headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}).status_code == 415
        answer.assert_not_called()


def test_rate_limit_ignores_forged_forwarded_ips_and_includes_cors():
    with TestClient(create_app(settings(DEMO_REQUESTS_PER_MINUTE=1))) as client:
        first = client.post("/api/answers", json={}, headers={"X-Forwarded-For": "1.2.3.4"})
        assert first.status_code == 422
        second = client.post("/api/answers", json={}, headers={"X-Forwarded-For": "5.6.7.8", "Origin": "http://localhost:5173"})
        assert second.status_code == 429 and second.headers["Retry-After"] == "60"
        assert second.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert client.get("/health").status_code == 200


def test_global_rate_daily_limit_and_window_reset():
    now = [0.0]
    runtime = DemoRuntime(settings(DEMO_GLOBAL_REQUESTS_PER_MINUTE=2, DEMO_GLOBAL_REQUESTS_PER_DAY=3), clock=lambda: now[0])
    assert runtime.admit("a") and runtime.admit("b")
    assert not runtime.admit("c")
    now[0] = 61
    assert runtime.admit("c")
    assert not runtime.admit("d")
    now[0] = 86462
    assert runtime.admit("d")


@pytest.fixture
def clients():
    with patch("app.integrations.search.DefaultAzureCredential"), patch("app.integrations.search.SearchClient") as factory:
        yield factory.return_value.__enter__.return_value


def test_search_enforces_uuid_filename_and_vector_prefilter(clients):
    clients.search.side_effect = [rows(), [{**evidence().model_dump(exclude={"search_score"}), "@search.score": .03}]]
    results = DemoSearchStore(settings()).hybrid_search("vacation", [.1] * 1536, 20)
    assert results == [evidence()]
    args = clients.search.call_args.kwargs
    assert args["filter"] == f"document_id eq '{DOCUMENT_ID}' and filename eq 'employee-handbook.pdf'"
    assert args["vector_filter_mode"] == "preFilter" and args["top"] == 5
    assert args["search_fields"] == ["text"] and len(args["vector_queries"]) == 1
    assert clients.search.call_args_list[0].kwargs["filter"] == f"document_id eq '{DOCUMENT_ID}'"


@pytest.mark.parametrize("invalid", [[], rows()[:5], [dict(r, filename="private.pdf") for r in rows()], rows() + [rows()[0]]])
def test_missing_invalid_demo_corpus_never_returns_evidence(invalid):
    with patch.object(SearchStore, "document_chunks", return_value=invalid), patch.object(SearchStore, "hybrid_search") as search:
        with pytest.raises(SearchError):
            DemoSearchStore(settings()).hybrid_search("policy", [.1] * 1536, 5)
        search.assert_not_called()


@pytest.mark.parametrize("change", [{"document_id": "private"}, {"filename": "private.pdf"}, {"page_number": 9}, {"chunk_id": "unknown"}])
def test_unexpected_search_evidence_is_rejected_before_generation(change):
    wrong = evidence().model_copy(update=change)
    with patch.object(SearchStore, "document_chunks", return_value=rows()), patch.object(SearchStore, "hybrid_search", return_value=[wrong]), patch("app.services.answers.AzureAnswerGenerator") as generator:
        with TestClient(create_app(settings())) as client:
            with patch("app.services.retrieval.embed_question", return_value=[.1]*1536):
                assert client.post("/api/answers", json={"question": "policy"}).status_code == 503
        generator.assert_not_called()


def test_anonymous_answer_reuses_pipeline_but_only_publishes_safe_citations():
    with patch.object(SearchStore, "document_chunks", return_value=rows()), patch.object(SearchStore, "hybrid_search", return_value=[evidence()]), patch("app.services.retrieval.embed_question", return_value=[.1]*1536), patch("app.services.answers.AzureAnswerGenerator") as generator:
        generator.return_value.generate.return_value = ModelAnswer(status="supported", answer="15 days.", source_ids=["S1"])
        with TestClient(create_app(settings())) as client:
            response = client.post("/api/answers", json={"question": "vacation"})
        assert response.status_code == 200
        assert response.json() == {"question": "vacation", "status": "supported", "answer": "15 days.",
            "citations": [{"filename": "employee-handbook.pdf", "page_number": 1}]}
        assert DOCUMENT_ID not in response.text and "chunk-1" not in response.text


def test_empty_scoped_retrieval_abstains():
    with patch.object(SearchStore, "document_chunks", return_value=rows()), patch.object(SearchStore, "hybrid_search", return_value=[]), patch("app.services.retrieval.embed_question", return_value=[.1]*1536), patch("app.services.answers.AzureAnswerGenerator") as generator:
        with TestClient(create_app(settings())) as client:
            result = client.post("/api/answers", json={"question": "Japan"}).json()
        assert result["status"] == "insufficient_evidence" and result["citations"] == []
        generator.assert_not_called()


def test_unexpected_worker_failure_does_not_leak_details():
    with patch("app.api.answers.answer_question", side_effect=RuntimeError("secret credential")), TestClient(create_app(settings())) as client:
        response = client.post("/api/answers", json={"question": "policy"})
        assert response.status_code == 503 and "secret" not in response.text


def test_timeout_holds_concurrency_until_worker_finishes():
    async def run():
        runtime = DemoRuntime(settings(DEMO_REQUEST_TIMEOUT_SECONDS=1))
        started, release = threading.Event(), threading.Event()
        def slow():
            started.set()
            release.wait(5)
            return "done"
        first = asyncio.create_task(runtime.execute(slow))
        await asyncio.to_thread(started.wait, 2)
        try:
            with pytest.raises(HTTPException) as busy:
                await runtime.execute(lambda: "should not run")
            assert busy.value.status_code == 429
            with pytest.raises(HTTPException) as timeout:
                await first
            assert timeout.value.status_code == 504 and runtime.active == 1
            with pytest.raises(HTTPException) as still_busy:
                await runtime.execute(lambda: "should not run")
            assert still_busy.value.status_code == 429
        finally:
            release.set()
        await asyncio.gather(*runtime.workers)
        await asyncio.sleep(0)
        assert runtime.active == 0
        assert await runtime.execute(lambda: "recovered") == "recovered"
    asyncio.run(run())


def test_streaming_body_limit_and_body_timeout():
    from app.access import PublicAccessMiddleware
    async def run():
        config = settings(DEMO_MAX_BODY_BYTES=128, DEMO_BODY_TIMEOUT_SECONDS=1)
        downstream = Mock()
        middleware = PublicAccessMiddleware(downstream, config, DemoRuntime(config))
        scope = dict(type="http", method="POST", path="/api/answers", headers=[(b"content-type", b"application/json")], query_string=b"", client=("peer", 1))
        messages = []
        async def send(message): messages.append(message)
        async def large(): return {"type": "http.request", "body": b"x"*129, "more_body": True}
        await middleware(scope, large, send)
        assert messages[0]["status"] == 413
        messages.clear()
        async def slow():
            await asyncio.sleep(2)
            return {"type": "http.request", "body": b"", "more_body": False}
        await middleware(scope, slow, send)
        assert messages[0]["status"] == 408
        downstream.assert_not_called()
    asyncio.run(run())


def test_public_embedding_transport_limits():
    from app.integrations.embeddings import AzureEmbeddings
    from types import SimpleNamespace
    with patch("app.integrations.embeddings.DefaultAzureCredential"), patch("app.integrations.embeddings.get_bearer_token_provider"), patch("app.integrations.embeddings.OpenAI") as factory:
        factory.return_value.__enter__.return_value.embeddings.create.return_value = SimpleNamespace(
            model="text-embedding-3-small", data=[SimpleNamespace(index=0, embedding=[.1]*1536)])
        AzureEmbeddings(settings(AZURE_EMBEDDING_ENDPOINT="https://models.example.com/openai/v1/", AZURE_EMBEDDING_DEPLOYMENT="embedding")).embed(["question"])
        assert factory.call_args.kwargs["timeout"] == 15
        assert factory.call_args.kwargs["max_retries"] == 0


def test_public_generation_transport_and_output_limits():
    from app.integrations.generation import AzureAnswerGenerator
    from types import SimpleNamespace
    with patch("app.integrations.generation.DefaultAzureCredential"), patch("app.integrations.generation.get_bearer_token_provider"), patch("app.integrations.generation.OpenAI") as factory:
        client = factory.return_value.__enter__.return_value
        client.chat.completions.create.return_value = SimpleNamespace(model="gpt-5-mini", choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(refusal=None, content=json.dumps(
                {"status":"supported", "answer":"15 days", "source_ids":["S1"]})))])
        AzureAnswerGenerator(settings(AZURE_GENERATION_ENDPOINT="https://models.example.com/openai/v1/", AZURE_GENERATION_DEPLOYMENT="mini")).generate("vacation", {"S1":"15 days"})
        assert factory.call_args.kwargs["timeout"] == 15
        assert factory.call_args.kwargs["max_retries"] == 0
        assert client.chat.completions.create.call_args.kwargs["max_completion_tokens"] == 2048


def test_search_transport_limits_and_custom_result_cap():
    with patch("app.integrations.search.DefaultAzureCredential"), patch("app.integrations.search.SearchClient") as factory:
        client = factory.return_value.__enter__.return_value
        client.search.side_effect = [rows(), []]
        DemoSearchStore(settings(DEMO_TOP_K=3)).hybrid_search("q", [.1]*1536, 20)
        assert factory.call_args.kwargs["connection_timeout"] == 15
        assert factory.call_args.kwargs["read_timeout"] == 15
        assert factory.call_args.kwargs["retry_total"] == 0
        assert client.search.call_args.kwargs["top"] == 3


def test_worker_api_error_and_invented_citation_are_safe():
    from app.integrations.generation import GenerationAPIError
    from app.models import AnswerResponse, Citation
    from unittest.mock import patch
    wrong = AnswerResponse(question="q", status="supported", answer="private answer",
        citations=[Citation(source_id="S1", document_id="other", filename="private.pdf", page_number=1, chunk_id="private", chunk_index=0)])
    for value in [GenerationAPIError("secret API response"), wrong]:
        kwargs = {"side_effect":value} if isinstance(value, Exception) else {"return_value":value}
        with patch("app.api.answers.answer_question", **kwargs), TestClient(create_app(settings())) as client:
            response = client.post("/api/answers", json={"question":"q"})
            assert response.status_code in (502, 503)
            assert "private" not in response.text and "secret" not in response.text


def test_http_deadline_response_and_overload_status():
    from unittest.mock import AsyncMock
    application = create_app(settings())
    with TestClient(application) as client:
        for status in (429, 504):
            with patch.object(application.state.demo_runtime, "execute", new=AsyncMock(side_effect=HTTPException(status, "Please try later"))):
                assert client.post("/api/answers", json={"question":"policy"}).status_code == status
