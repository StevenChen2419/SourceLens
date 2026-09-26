from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_environment_overrides_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("KNOWLEDGEOPS_APP_NAME=From file\n", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGEOPS_APP_NAME", "From environment")

    assert Settings(_env_file=env_file).app_name == "From environment"


def test_empty_app_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGEOPS_APP_NAME", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_storage_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net")
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "documents")
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "12")
    settings = Settings(_env_file=None)
    assert settings.azure_storage_account_url == "https://example.blob.core.windows.net"
    assert settings.azure_storage_container == "documents"
    assert settings.max_upload_size_mb == 12


@pytest.mark.parametrize("url", [
    "http://example.blob.core.windows.net",
    "https://example.blob.core.windows.net?sig=secret",
    "https://user:secret@example.blob.core.windows.net",
    "https://example.blob.core.windows.net/documents",
])
def test_invalid_storage_urls_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, AZURE_STORAGE_ACCOUNT_URL=url)


def test_empty_storage_url_is_optional() -> None:
    assert Settings(_env_file=None, AZURE_STORAGE_ACCOUNT_URL="").azure_storage_account_url is None


def test_invalid_upload_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, MAX_UPLOAD_SIZE_MB=0)
