"""Typed application settings loaded from the environment or backend/.env."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        env_prefix="KNOWLEDGEOPS_",
    )

    app_name: str = Field(default="KnowledgeOps", min_length=1)
