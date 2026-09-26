"""Deterministic token windows that never cross PDF page boundaries."""

import hashlib
import json
from collections.abc import Iterator, Sequence
from typing import Self

import tiktoken
from pydantic import BaseModel, Field, model_validator

from app.models import DocumentChunk, PDFPage

TOKEN_ENCODING = "cl100k_base"


class ChunkingOptions(BaseModel):
    max_tokens: int = Field(default=500, gt=0, strict=True)
    overlap_tokens: int = Field(default=75, ge=0, strict=True)

    @model_validator(mode="after")
    def validate_overlap(self) -> Self:
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        return self


def _token_windows(
    text: str, encoding: tiktoken.Encoding, options: ChunkingOptions
) -> Iterator[str]:
    # Document content resembling special tokens is still ordinary source text.
    tokens = encoding.encode_ordinary(text)
    start = 0
    while start < len(tokens):
        end = min(start + options.max_tokens, len(tokens))
        while end > start:
            try:
                chunk_text = encoding.decode(tokens[start:end], errors="strict")
            except UnicodeDecodeError:
                # A token boundary can fall inside a multi-byte character.
                end -= 1
                continue
            # Retokenizing a substring can differ from the original token slice.
            if len(encoding.encode_ordinary(chunk_text)) <= options.max_tokens:
                break
            end -= 1
        else:
            raise ValueError(
                "max_tokens is too small to preserve a complete Unicode character."
            )

        yield chunk_text
        if end == len(tokens):
            break

        next_start = max(start + 1, end - options.overlap_tokens)
        while next_start < end:
            try:
                encoding.decode(tokens[next_start:end], errors="strict")
                break
            except UnicodeDecodeError:
                next_start += 1
        start = next_start


def chunk_pages(
    pages: Sequence[PDFPage],
    *,
    document_id: str,
    filename: str,
    options: ChunkingOptions | None = None,
) -> list[DocumentChunk]:
    """Chunk pages in input order, using a zero-based document-wide chunk index.

    Reuse the document ID, page text/order, tokenizer, and options for stable IDs.
    Empty pages produce no chunks. Overlap may shrink to preserve Unicode and
    guarantee forward progress, but page text is never dropped between windows.
    """
    if not document_id.strip() or not filename.strip():
        raise ValueError("document_id and filename must not be blank")

    options = options or ChunkingOptions()
    encoding = tiktoken.get_encoding(TOKEN_ENCODING)
    chunks: list[DocumentChunk] = []
    for page in pages:
        if not page.text.strip():
            continue
        for text in _token_windows(page.text, encoding, options):
            chunk_index = len(chunks)
            identity = json.dumps(
                [document_id, page.page_number, chunk_index, text],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            chunks.append(
                DocumentChunk(
                    document_id=document_id,
                    filename=filename,
                    page_number=page.page_number,
                    chunk_id=chunk_id,
                    chunk_index=chunk_index,
                    text=text,
                )
            )
    return chunks
