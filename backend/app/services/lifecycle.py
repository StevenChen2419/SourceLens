"""Document identity, legacy adoption, duplicate prevention, and retryable deletion."""

import hashlib
from typing import BinaryIO
from uuid import UUID

from app.config import Settings
from app.integrations.blob import BlobStore
from app.integrations.catalog import CatalogStore, CatalogSession, CatalogError, DocumentRecord
from app.integrations.search import SearchStore
from app.models import DocumentSummary, DocumentUploadResponse, DocumentDeleteResponse
from app.services.documents import prepare_upload, ingest_prepared
from app.services.pdf import extract_pdf_pages
from app.services.chunking import chunk_pages


class DuplicateDocumentError(ValueError):
    def __init__(self, record: DocumentRecord) -> None:
        self.record = record
        super().__init__("Identical PDF already registered. No new chunks were indexed.")


class DocumentNotFoundError(ValueError):
    pass


def initialize_catalog(session: CatalogSession, blob: BlobStore, search: SearchStore) -> None:
    if session.data.initialized:
        return
    grouped: dict[str, list[dict]] = {}
    for row in search.document_chunks():
        grouped.setdefault(str(UUID(row["document_id"])), []).append(row)
    for document_id, rows in grouped.items():
        original = blob.read_pdf(UUID(document_id))
        page_count = None  # Missing originals cannot establish total pages (including empty ones).
        keys = {row["chunk_id"] for row in rows}
        expected_count = len(rows)
        complete = False
        content_hash = None
        if original:
            try:
                pages = extract_pdf_pages(original)
                expected = chunk_pages(pages, document_id=document_id, filename=rows[0]["filename"])
            except ValueError:
                # An unreadable legacy original must still be listable/removable.
                expected = []
            else:
                content_hash = hashlib.sha256(original).hexdigest()
                page_count = len(pages)
                expected_count = len(expected)
                actual_metadata = {(row["chunk_id"], row["filename"], row["page_number"], row["chunk_index"]) for row in rows}
                expected_metadata = {(chunk.chunk_id, chunk.filename, chunk.page_number, chunk.chunk_index) for chunk in expected}
                complete = actual_metadata == expected_metadata
                keys.update(chunk.chunk_id for chunk in expected)
        session.data.documents[document_id] = DocumentRecord(
            document_id=UUID(document_id), filename=rows[0]["filename"],
            content_hash=content_hash,
            page_count=page_count, chunk_count=expected_count, chunk_ids=sorted(keys),
            state="indexed" if complete else "failed", original_owned=False,
        )
    session.data.initialized = True
    session.save()


def list_documents(catalog: CatalogStore, blob: BlobStore, search: SearchStore) -> list[DocumentSummary]:
    data = catalog.read()
    if not data.initialized:
        with catalog.locked() as session:
            initialize_catalog(session, blob, search)
            data = session.data
    return [DocumentSummary(
        **record.model_dump(include={"document_id", "filename", "page_count", "chunk_count", "state"}),
        original_retained_on_delete=not record.original_owned,
    ) for record in data.documents.values() if record.state != "deleted"]


def upload_managed_document(*, file: BinaryIO, filename: str, content_type: str | None, max_bytes: int,
                            blob_store: BlobStore, settings: Settings, search_store: SearchStore,
                            catalog: CatalogStore) -> DocumentUploadResponse:
    prepared = prepare_upload(file=file, filename=filename, content_type=content_type, max_bytes=max_bytes)
    digest = hashlib.sha256(prepared.data).hexdigest()
    with catalog.locked() as session:
        initialize_catalog(session, blob_store, search_store)
        for record in session.data.documents.values():
            if record.state != "deleted" and record.content_hash == digest:
                raise DuplicateDocumentError(record)
        if any(record.state != "deleted" and record.content_hash is None for record in session.data.documents.values()):
            raise CatalogError("An existing original PDF is missing or unreadable; duplicate detection cannot be guaranteed. Delete its incomplete document record before uploading again.")
        record = DocumentRecord(
            document_id=prepared.document_id, filename=filename, content_hash=digest,
            page_count=prepared.page_count, chunk_count=len(prepared.chunks),
            chunk_ids=[chunk.chunk_id for chunk in prepared.chunks], state="indexing", original_owned=True,
        )
        session.data.documents[str(record.document_id)] = record
        session.save()  # Persist ownership and keys before any original/index write.
        try:
            response = ingest_prepared(prepared, blob_store=blob_store, settings=settings, search_store=search_store)
        except Exception:
            record.state = "failed"
            session.save()
            raise
        record.state = "indexed"
        session.save()
        return response


def delete_document(document_id: UUID, catalog: CatalogStore, blob: BlobStore,
                    search: SearchStore) -> DocumentDeleteResponse:
    with catalog.locked() as session:
        initialize_catalog(session, blob, search)
        record = session.data.documents.get(str(document_id))
        if record is None:
            raise DocumentNotFoundError("Document not found.")
        # Recheck even a tombstone: retries can clean up late-visible Search writes.
        record.state = "deleting"
        session.save()
        search.delete_document_chunks(document_id, record.chunk_ids)
        if record.original_owned:
            blob.delete_pdf(document_id)
        record.state = "deleted"
        session.save()
        return DocumentDeleteResponse(document_id=document_id, original_retained=not record.original_owned)
