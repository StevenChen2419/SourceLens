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
