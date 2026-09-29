"""Source text and chunk metadata shared by the local document services."""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class PDFPage(BaseModel):
    page_number: int = Field(ge=1)
    text: str


class DocumentChunk(BaseModel):
    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    chunk_id: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    filename: str
    page_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)


class EmbeddedChunk(BaseModel):
    chunk: DocumentChunk
    vector: list[float]
    model: str
    deployment: str
    dimensions: int = Field(gt=0)


class RetrievalRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000, strict=True)
    top_k: int | None = Field(default=None, ge=1, le=20, strict=True)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must contain non-whitespace text")
        return value


class RetrievedChunk(DocumentChunk):
    search_score: float | None = Field(default=None, allow_inf_nan=False)


class RetrievalResponse(BaseModel):
    question: str
    results: list[RetrievedChunk]
