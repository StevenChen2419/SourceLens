"""Human-reviewed ground truth and deliberately limited deterministic checks."""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    filename: str = Field(min_length=1)
    page_number: int = Field(ge=1)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    category: Literal["direct", "paraphrase", "semantic", "unsupported"]
    section: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=4000)
    expected_status: Literal["supported", "insufficient_evidence"]
    expected_sources: list[Source] = Field(default_factory=list)
    criterion: str = Field(min_length=1)
    # Every required regex must match; no forbidden regex may match.
    required_patterns: list[str] = Field(default_factory=list)
    forbidden_patterns: list[str] = Field(default_factory=list)

    @field_validator("id", "section", "question", "criterion")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("required_patterns", "forbidden_patterns")
    @classmethod
    def valid_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            if not pattern.strip():
                raise ValueError("empty pattern")
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError("invalid regular expression") from error
        return patterns

    @model_validator(mode="after")
    def consistent_expectations(self) -> "Case":
        supported = self.expected_status == "supported"
        if supported != bool(self.expected_sources):
            raise ValueError("supported cases require sources; abstentions must not have sources")
        if (self.category == "unsupported") == supported:
            raise ValueError("unsupported category must expect abstention")
        if not supported and (self.required_patterns or self.forbidden_patterns):
            raise ValueError("abstention is checked by status, canonical message, and empty citations")
        if len(set(self.expected_sources)) != len(self.expected_sources):
            raise ValueError("duplicate expected sources")
        return self


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> "Dataset":
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be unique")
        return self


def load_dataset(path: Path) -> Dataset:
    return Dataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
