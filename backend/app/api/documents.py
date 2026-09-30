"""Document upload HTTP contract; processing lives in the document service."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.config import Settings
from app.dependencies import get_blob_store, get_settings, get_search_store, get_catalog_store
from app.integrations.catalog import CatalogStore, CatalogError, CatalogBusyError
from app.services.lifecycle import upload_managed_document, list_documents, delete_document, DuplicateDocumentError, DocumentNotFoundError
from app.integrations.search import SearchError, SearchStore
from app.integrations.embeddings import EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError
from app.services.documents import IngestionIndexingError
from app.integrations.blob import BlobStore, BlobUploadError
from app.models import DocumentUploadResponse, DocumentListResponse, DocumentDeleteResponse
from app.services.documents import InvalidPDFUploadError, UploadTooLargeError
from app.services.pdf import MalformedPDFError, UnsupportedPDFError

router = APIRouter(prefix="/api/documents", tags=["documents"])


async def require_single_file(request: Request) -> None:
    # Scalar UploadFile binding otherwise silently selects one repeated field.
    form = await request.form()
    if len(form.getlist("file")) != 1:
        raise HTTPException(status_code=422, detail="Provide exactly one PDF file.")


@router.post(
    "", response_model=DocumentUploadResponse, status_code=201,
    dependencies=[Depends(require_single_file)],
)
def create_document(
    # Keep Swagger's binary-file hint alongside FastAPI's contentMediaType schema.
    file: Annotated[UploadFile, File(..., json_schema_extra={"format": "binary"})],
    settings: Annotated[Settings, Depends(get_settings)],
    blob_store: Annotated[BlobStore, Depends(get_blob_store)],
    search_store: Annotated[SearchStore, Depends(get_search_store)],
    catalog: Annotated[CatalogStore, Depends(get_catalog_store)],
) -> DocumentUploadResponse:
    # A synchronous route runs parsing, tokenization, and Azure I/O in a worker
    # thread rather than blocking the async event loop. Exactly one file is allowed.
    try:
        return upload_managed_document(
            file=file.file,
            filename=file.filename or "",
            content_type=file.content_type,
            max_bytes=settings.max_upload_size_mb * 1024 * 1024,
            blob_store=blob_store,
            settings=settings,
            search_store=search_store,
            catalog=catalog,
        )
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "duplicate_document", "document_id": str(exc.record.document_id),
            "filename": exc.record.filename, "state": exc.record.state,
            "message": str(exc),
        }) from exc
    except CatalogBusyError as exc:
        raise HTTPException(status_code=409, detail={"code": "document_operation_busy", "message": str(exc)}) from exc
    except CatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except InvalidPDFUploadError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except (MalformedPDFError, UnsupportedPDFError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IngestionIndexingError as exc:
        raise HTTPException(status_code=503, detail={
            "message": str(exc), "document_id": exc.document_id,
        }) from exc
    except (SearchError, EmbeddingAPIError, EmbeddingConfigurationError, EmbeddingResponseError) as exc:
        raise HTTPException(status_code=503, detail="Document embedding/indexing is unavailable. Check server configuration and Azure services.") from exc
    except BlobUploadError as exc:
        raise HTTPException(
            status_code=503, detail="Document storage is unavailable. Please try again later."
        ) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Existing document metadata could not be read. Check the document catalog.") from exc


@router.get("", response_model=DocumentListResponse)
def get_documents(
    catalog: Annotated[CatalogStore, Depends(get_catalog_store)],
    blob: Annotated[BlobStore, Depends(get_blob_store)],
    search: Annotated[SearchStore, Depends(get_search_store)],
) -> DocumentListResponse:
    try:
        return DocumentListResponse(documents=list_documents(catalog, blob, search))
    except CatalogBusyError as exc:
        raise HTTPException(status_code=409, detail="Document operation in progress. Retry later.") from exc
    except (CatalogError, BlobUploadError, SearchError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Document listing is unavailable. Check storage and existing document metadata.") from exc


@router.delete("/{document_id}", response_model=DocumentDeleteResponse)
def remove_document(
    document_id: UUID,
    catalog: Annotated[CatalogStore, Depends(get_catalog_store)],
    blob: Annotated[BlobStore, Depends(get_blob_store)],
    search: Annotated[SearchStore, Depends(get_search_store)],
) -> DocumentDeleteResponse:
    try:
        return delete_document(document_id, catalog, blob, search)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document not found.") from exc
    except CatalogBusyError as exc:
        raise HTTPException(status_code=409, detail="Document operation in progress. Retry later.") from exc
    except (CatalogError, BlobUploadError, SearchError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Deletion is incomplete. Some data may remain; refresh the list and retry deletion.") from exc
