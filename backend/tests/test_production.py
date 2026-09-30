"""Deployment configuration checks without Azure calls."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def production_values():
    return dict(
        environment="production",
        cors_origins=["https://knowledgeops.example.com"],
        AZURE_TOKEN_CREDENTIALS="ManagedIdentityCredential",
        AZURE_STORAGE_ACCOUNT_URL="https://storage.example.com",
        AZURE_SEARCH_ENDPOINT="https://search.example.com",
        AZURE_EMBEDDING_ENDPOINT="https://models.example.com/openai/v1/",
        AZURE_EMBEDDING_DEPLOYMENT="embedding",
        AZURE_GENERATION_ENDPOINT="https://models.example.com/openai/v1/",
        AZURE_GENERATION_DEPLOYMENT="generation",
    )


@pytest.mark.parametrize("missing", [
    "AZURE_STORAGE_ACCOUNT_URL", "AZURE_SEARCH_ENDPOINT", "AZURE_EMBEDDING_ENDPOINT",
    "AZURE_EMBEDDING_DEPLOYMENT", "AZURE_GENERATION_ENDPOINT", "AZURE_GENERATION_DEPLOYMENT",
])
def test_production_requires_all_services(missing):
    values = production_values()
    values[missing] = None
    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("credential", [None, "AzureCliCredential", "EnvironmentCredential"])
def test_production_requires_managed_identity(credential):
    values = production_values()
    values["AZURE_TOKEN_CREDENTIALS"] = credential
    with pytest.raises(ValidationError, match="ManagedIdentityCredential"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("origin", [
    "*", "https://*.example.com", "https://example.com/path", "https://example.com/",
    "https://user:password@example.com", "https://example.com?key=x", "https://example.com#x",
    "https://example.com:99999", "https://example.com:", " https://example.com",
])
def test_origins_must_be_exact(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


@pytest.mark.parametrize("origin", [
    "http://example.com", "https://localhost", "https://127.0.0.1", "https://[::1]",
])
def test_production_disallows_http_and_loopback(origin):
    values = production_values()
    values["cors_origins"] = [origin]
    with pytest.raises(ValidationError, match="Production CORS"):
        Settings(_env_file=None, **values)


def test_cors_reads_json_environment(monkeypatch):
    monkeypatch.setenv("KNOWLEDGEOPS_CORS_ORIGINS", '["https://frontend.example.com"]')
    assert Settings(_env_file=None).cors_origins == ["https://frontend.example.com"]


def test_production_default_local_origins_are_rejected():
    values = production_values()
    del values["cors_origins"]
    with pytest.raises(ValidationError, match="Production CORS"):
        Settings(_env_file=None, **values)


def test_production_health_and_exact_cors_without_azure(monkeypatch):
    def no_azure(*args, **kwargs):
        pytest.fail("Health and CORS must not create Azure credentials")

    for integration in ["blob", "catalog", "search", "embeddings", "generation"]:
        monkeypatch.setattr(f"app.integrations.{integration}.DefaultAzureCredential", no_azure)
    application = create_app(Settings(_env_file=None, **production_values()))
    with TestClient(application) as client:
        assert client.get("/health").json() == {"status": "ok"}
        for method in ["GET", "POST", "DELETE"]:
            for origin, status in [
                ("https://knowledgeops.example.com", 200),
                ("https://knowledgeops.example.com.evil.example", 400),
                ("http://localhost:5173", 400),
            ]:
                response = client.options("/api/documents", headers={
                    "Origin": origin, "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": "content-type",
                })
                assert response.status_code == status
                assert "access-control-allow-credentials" not in response.headers
                if status == 200:
                    assert response.headers["access-control-allow-origin"] == origin
                else:
                    assert "access-control-allow-origin" not in response.headers
