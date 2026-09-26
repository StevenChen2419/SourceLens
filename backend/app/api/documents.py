"""Document upload HTTP contract; processing lives in the document service."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.config import Settings
from app.dependencies import get_blob_store, get_settings
from app.integrations.blob import BlobStore, BlobUploadError
from app.models import DocumentUploadResponse
from app.services.documents import InvalidPDFUploadError, UploadTooLargeError, upload_document
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
) -> DocumentUploadResponse:
    # A synchronous route runs parsing, tokenization, and Azure I/O in a worker
    # thread rather than blocking the async event loop. Exactly one file is allowed.
    try:
        return upload_document(
            file=file.file,
            filename=file.filename or "",
            content_type=file.content_type,
            max_bytes=settings.max_upload_size_mb * 1024 * 1024,
            blob_store=blob_store,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except InvalidPDFUploadError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except (MalformedPDFError, UnsupportedPDFError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BlobUploadError as exc:
        raise HTTPException(
            status_code=503, detail="Document storage is unavailable. Please try again later."
        ) from exc
