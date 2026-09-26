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
