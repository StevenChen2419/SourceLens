"""Validate, extract, chunk, and store one original PDF."""

from dataclasses import dataclass
from typing import BinaryIO
from uuid import UUID, uuid4

from app.integrations.blob import BlobStore
from app.config import Settings
from app.integrations.search import SearchError, SearchStore
from app.services.embeddings import embed_chunks
from app.models import DocumentChunk, DocumentUploadResponse
from app.services.chunking import chunk_pages
from app.services.pdf import extract_pdf_pages


class InvalidPDFUploadError(ValueError):
    """The upload does not identify a PDF."""


class UploadTooLargeError(ValueError):
    """The uploaded file exceeds the configured byte limit."""


class IngestionIndexingError(RuntimeError):
    def __init__(self, document_id: str) -> None:
        self.document_id = document_id
        super().__init__("Original PDF stored, but indexing failed; some chunks may exist.")


@dataclass
class PreparedDocument:
    document_id: UUID
    filename: str
    data: bytes
    page_count: int
    chunks: list[DocumentChunk]


def prepare_upload(*, file: BinaryIO, filename: str, content_type: str | None, max_bytes: int) -> PreparedDocument:
    if (
        not filename or len(filename) > 255
        or not filename.lower().endswith(".pdf")
        or content_type not in ("application/pdf", "application/octet-stream")
    ):
        raise InvalidPDFUploadError("Provide a PDF file with a filename of at most 255 characters.")

    # Read at most one byte beyond the limit; never trust Content-Length.
    data = file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UploadTooLargeError("The PDF exceeds the configured upload size limit.")
    if not data.startswith(b"%PDF-"):
        raise InvalidPDFUploadError("The uploaded file does not have a PDF signature.")

    document_id = uuid4()
    pages = extract_pdf_pages(data)
    chunks = chunk_pages(pages, document_id=str(document_id), filename=filename)
    return PreparedDocument(document_id, filename, data, len(pages), chunks)


def ingest_prepared(document: PreparedDocument, *, blob_store: BlobStore, settings: Settings,
                    search_store: SearchStore) -> DocumentUploadResponse:
    document_id, filename, data, chunks = document.document_id, document.filename, document.data, document.chunks
    search_store.ensure_index()
    embedded_chunks = embed_chunks(chunks, settings)
    blob_store.upload_pdf(document_id=document_id, filename=filename, data=data)
    try:
        search_store.index_chunks(embedded_chunks)
    except SearchError as exc:
        raise IngestionIndexingError(str(document_id)) from exc
    return DocumentUploadResponse(
        document_id=document_id,
        filename=filename,
        page_count=document.page_count,
        chunk_count=len(chunks),
    )


def upload_document(*, file: BinaryIO, filename: str, content_type: str | None, max_bytes: int,
                    blob_store: BlobStore, settings: Settings, search_store: SearchStore) -> DocumentUploadResponse:
    """Original ingestion entrypoint retained for the isolated controlled experiment."""
    document = prepare_upload(file=file, filename=filename, content_type=content_type, max_bytes=max_bytes)
    return ingest_prepared(document, blob_store=blob_store, settings=settings, search_store=search_store)
