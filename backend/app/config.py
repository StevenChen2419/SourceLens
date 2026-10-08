"""Typed application settings loaded from the environment or backend/.env."""

from pathlib import Path
from ipaddress import ip_address
from typing import Literal, Self
from uuid import UUID
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        env_prefix="KNOWLEDGEOPS_",
    )

    app_mode: Literal["development", "public_demo"] | None = Field(default=None, validation_alias="APP_MODE")
    demo_document_id: UUID | None = Field(default=None, validation_alias="DEMO_DOCUMENT_ID")
    demo_requests_per_minute: int = Field(default=5, ge=1, le=20, validation_alias="DEMO_REQUESTS_PER_MINUTE")
    demo_global_requests_per_minute: int = Field(default=20, ge=1, le=60, validation_alias="DEMO_GLOBAL_REQUESTS_PER_MINUTE")
    demo_global_requests_per_day: int = Field(default=100, ge=1, le=500, validation_alias="DEMO_GLOBAL_REQUESTS_PER_DAY")
    demo_max_concurrency: int = Field(default=1, ge=1, le=2, validation_alias="DEMO_MAX_CONCURRENCY")
    demo_max_question_chars: int = Field(default=500, ge=1, le=1000, validation_alias="DEMO_MAX_QUESTION_CHARS")
    demo_max_body_bytes: int = Field(default=8192, ge=128, le=16384, validation_alias="DEMO_MAX_BODY_BYTES")
    demo_top_k: int = Field(default=5, ge=1, le=5, validation_alias="DEMO_TOP_K")
    demo_max_completion_tokens: int = Field(default=2048, ge=512, le=4096, validation_alias="DEMO_MAX_COMPLETION_TOKENS")
    demo_request_timeout_seconds: float = Field(default=45, ge=1, le=90, validation_alias="DEMO_REQUEST_TIMEOUT_SECONDS")
    demo_body_timeout_seconds: float = Field(default=5, ge=1, le=10, validation_alias="DEMO_BODY_TIMEOUT_SECONDS")
    demo_azure_timeout_seconds: float = Field(default=15, ge=1, le=30, validation_alias="DEMO_AZURE_TIMEOUT_SECONDS")
    demo_azure_max_retries: int = Field(default=0, ge=0, le=1, validation_alias="DEMO_AZURE_MAX_RETRIES")

    @model_validator(mode="after")
    def validate_access_mode(self) -> Self:
        if self.environment == "production" and self.app_mode != "public_demo":
            raise ValueError("Production requires explicit APP_MODE=public_demo; development and authenticated modes are not public modes")
        if self.app_mode is None:
            self.app_mode = "development"
        if self.app_mode == "public_demo":
            if self.demo_document_id is None:
                raise ValueError("Public demo requires DEMO_DOCUMENT_ID for the approved employee-handbook.pdf")
            if self.azure_search_index_name == "knowledgeops-eval-chunks":
                raise ValueError("The historical evaluation index cannot host the public demo")
            if not self.azure_search_endpoint:
                raise ValueError("Public demo requires AZURE_SEARCH_ENDPOINT")
        return self

    app_name: str = Field(default="SourceLens", min_length=1)
    environment: Literal["development", "production"] = "development"
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:5173", "http://127.0.0.1:5173",
    ], min_length=1)
    azure_token_credentials: str | None = Field(default=None, validation_alias="AZURE_TOKEN_CREDENTIALS")

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            url = urlsplit(origin)
            if (origin != origin.strip() or url.scheme not in ("http", "https")
                    or not url.hostname or url.username or url.password or "*" in origin
                    or url.path or url.query or url.fragment or origin.endswith(":")
                    or any(character.isspace() for character in origin)):
                raise ValueError("CORS origins must be exact HTTP(S) origins without paths, wildcards, or credentials")
            _ = url.port  # Reject malformed/out-of-range ports.
        return list(dict.fromkeys(origins))

    @model_validator(mode="after")
    def validate_production(self) -> Self:
        if self.environment != "production":
            return self
        required = {
            "AZURE_STORAGE_ACCOUNT_URL": self.azure_storage_account_url,
            "AZURE_SEARCH_ENDPOINT": self.azure_search_endpoint,
            "AZURE_EMBEDDING_ENDPOINT": self.azure_embedding_endpoint,
            "AZURE_EMBEDDING_DEPLOYMENT": self.azure_embedding_deployment,
            "AZURE_GENERATION_ENDPOINT": self.azure_generation_endpoint,
            "AZURE_GENERATION_DEPLOYMENT": self.azure_generation_deployment,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("Missing production configuration: " + ", ".join(missing))
        if self.azure_token_credentials != "ManagedIdentityCredential":
            raise ValueError("Production requires AZURE_TOKEN_CREDENTIALS=ManagedIdentityCredential")
        for origin in self.cors_origins:
            url = urlsplit(origin)
            hostname = url.hostname or ""
            try:
                loopback = ip_address(hostname).is_loopback
            except ValueError:
                loopback = hostname == "localhost" or hostname.endswith(".localhost")
            if url.scheme != "https" or loopback:
                raise ValueError("Production CORS origins must use HTTPS and must not be localhost")
        return self
    azure_generation_endpoint: str | None = Field(default=None, validation_alias="AZURE_GENERATION_ENDPOINT")
    azure_generation_deployment: str | None = Field(default=None, validation_alias="AZURE_GENERATION_DEPLOYMENT")
    generation_context_tokens: int = Field(default=8000, ge=500, le=20000, validation_alias="GENERATION_CONTEXT_TOKENS")
    generation_max_completion_tokens: int = Field(default=4096, ge=512, le=16384, validation_alias="GENERATION_MAX_COMPLETION_TOKENS")
    retrieval_top_k: int = Field(default=5, ge=1, le=20, validation_alias="RETRIEVAL_TOP_K")
    azure_search_endpoint: str | None = Field(default=None, validation_alias="AZURE_SEARCH_ENDPOINT")
    azure_search_index_name: str = Field(
        default="knowledgeops-chunks", validation_alias="AZURE_SEARCH_INDEX_NAME",
        min_length=2, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$",
    )

    @field_validator("azure_search_endpoint", mode="before")
    @classmethod
    def validate_search_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        url = urlsplit(value.strip())
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ("", "/")):
            raise ValueError("AZURE_SEARCH_ENDPOINT must be an HTTPS service URL without credentials or a path")
        return value.strip().rstrip("/")
    azure_embedding_endpoint: str | None = Field(default=None, validation_alias="AZURE_EMBEDDING_ENDPOINT")
    azure_embedding_deployment: str | None = Field(default=None, validation_alias="AZURE_EMBEDDING_DEPLOYMENT")
    embedding_dimensions: int = Field(default=1536, ge=1, le=1536, validation_alias="EMBEDDING_DIMENSIONS")
    embedding_batch_size: int = Field(default=16, ge=1, le=32, validation_alias="EMBEDDING_BATCH_SIZE")

    @field_validator("azure_embedding_endpoint", "azure_generation_endpoint", mode="before")
    @classmethod
    def validate_embedding_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        url = urlsplit(value.strip())
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path.rstrip("/") != "/openai/v1"):
            raise ValueError("Model endpoint must be an HTTPS /openai/v1/ URL without credentials")
        return value.strip().rstrip("/") + "/"

    @field_validator("azure_embedding_deployment", "azure_generation_deployment", mode="before")
    @classmethod
    def validate_embedding_deployment(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if any(character.isspace() for character in value) or "/" in value:
            raise ValueError("Model deployment must be a deployment name")
        return value
    azure_storage_account_url: str | None = Field(
        default=None, validation_alias="AZURE_STORAGE_ACCOUNT_URL"
    )
    azure_storage_container: str = Field(
        default="documents", validation_alias="AZURE_STORAGE_CONTAINER",
        min_length=3, max_length=63, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    max_upload_size_mb: int = Field(
        default=10, gt=0, validation_alias="MAX_UPLOAD_SIZE_MB"
    )

    @field_validator("azure_storage_account_url", mode="before")
    @classmethod
    def validate_account_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        url = urlsplit(value)
        if (
            url.scheme != "https" or not url.hostname
            or url.username or url.password or url.query or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("Use an HTTPS Blob service URL without credentials or a path")
        return value.rstrip("/")
