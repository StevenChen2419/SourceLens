"""Typed application settings loaded from the environment or backend/.env."""

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        env_prefix="KNOWLEDGEOPS_",
    )

    app_name: str = Field(default="KnowledgeOps", min_length=1)
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
