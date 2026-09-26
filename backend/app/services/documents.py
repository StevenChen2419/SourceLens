"""Validate, extract, chunk, and store one original PDF."""

from typing import BinaryIO
from uuid import uuid4

from app.integrations.blob import BlobStore
from app.models import DocumentUploadResponse
from app.services.chunking import chunk_pages
from app.services.pdf import extract_pdf_pages


class InvalidPDFUploadError(ValueError):
    """The upload does not identify a PDF."""


class UploadTooLargeError(ValueError):
    """The uploaded file exceeds the configured byte limit."""


def upload_document(
    *,
    file: BinaryIO,
    filename: str,
    content_type: str | None,
    max_bytes: int,
    blob_store: BlobStore,
) -> DocumentUploadResponse:
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
    blob_store.upload_pdf(document_id=document_id, filename=filename, data=data)
    return DocumentUploadResponse(
        document_id=document_id,
        filename=filename,
        page_count=len(pages),
        chunk_count=len(chunks),
    )
