"""Source text and chunk metadata shared by the local document services."""

from pydantic import BaseModel, Field


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
