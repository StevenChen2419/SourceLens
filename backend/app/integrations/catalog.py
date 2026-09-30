"""Small per-index document ledger, serialized by an Azure Blob lease."""

import hashlib
from contextlib import contextmanager
from collections.abc import Iterator
from uuid import UUID
from typing import Literal

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError, HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, BlobClient, BlobLeaseClient, ContentSettings
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.config import Settings


class CatalogError(RuntimeError):
    pass


class CatalogBusyError(CatalogError):
    pass


class DocumentRecord(BaseModel):
    document_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_hash: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int | None = Field(ge=1)
    chunk_count: int = Field(ge=1)
    chunk_ids: list[str]
    state: Literal["indexing", "indexed", "failed", "deleting", "deleted"]
    original_owned: bool = False

    @model_validator(mode="after")
    def valid_keys(self) -> "DocumentRecord":
        if not self.chunk_ids or len(set(self.chunk_ids)) != len(self.chunk_ids) or any(not key for key in self.chunk_ids):
            raise ValueError("Document must have unique nonempty chunk keys")
        return self


class Catalog(BaseModel):
    initialized: bool = False
    documents: dict[str, DocumentRecord] = Field(default_factory=dict)


def decode_catalog(data: bytes) -> Catalog:
    try:
        catalog = Catalog.model_validate_json(data)
        if any(key != str(record.document_id) for key, record in catalog.documents.items()):
            raise ValueError("Document key mismatch")
        return catalog
    except (ValidationError, ValueError) as exc:
        raise CatalogError("Document catalog is invalid; operator review is required.") from exc


class CatalogSession:
    def __init__(self, blob: BlobClient, lease: BlobLeaseClient, data: Catalog) -> None:
        self.blob = blob
        self.lease = lease
        self.data = data

    def save(self) -> None:
        try:
            self.blob.upload_blob(self.data.model_dump_json().encode(), overwrite=True, lease=self.lease,
                                  content_settings=ContentSettings(content_type="application/json"))
        except AzureError as exc:
            raise CatalogError("Document state could not be saved; operations may be incomplete.") from exc


class CatalogStore:
    def __init__(self, settings: Settings) -> None:
        self.account_url = settings.azure_storage_account_url
        self.container = settings.azure_storage_container
        scope = hashlib.sha256((settings.azure_search_endpoint or "").lower().rstrip("/").encode()).hexdigest()
        self.blob_name = f"catalogs/{scope}/{settings.azure_search_index_name}.json"

    def _require_storage(self) -> None:
        if not self.account_url:
            raise CatalogError("Document storage is not configured.")

    def read(self) -> Catalog:
        self._require_storage()
        try:
            with DefaultAzureCredential() as credential, BlobServiceClient(self.account_url, credential=credential) as client:
                blob = client.get_blob_client(self.container, self.blob_name)
                try:
                    return decode_catalog(blob.download_blob().readall())
                except ResourceNotFoundError:
                    return Catalog()
        except AzureError as exc:
            raise CatalogError("Document catalog is unavailable.") from exc

    @contextmanager
    def locked(self) -> Iterator[CatalogSession]:
        self._require_storage()
        try:
            with DefaultAzureCredential() as credential, BlobServiceClient(self.account_url, credential=credential) as client:
                blob = client.get_blob_client(self.container, self.blob_name)
                try:
                    blob.get_blob_properties()
                except ResourceNotFoundError:
                    try:
                        blob.upload_blob(Catalog().model_dump_json().encode(), overwrite=False,
                                         content_settings=ContentSettings(content_type="application/json"))
                    except ResourceExistsError:
                        pass  # Another process created the ledger. Never replace it.
                try:
                    lease = blob.acquire_lease(lease_duration=-1)
                except HttpResponseError as exc:
                    if exc.status_code == 409:
                        raise CatalogBusyError("Another document operation is active. Retry later; a stale lease needs operator recovery.") from exc
                    raise
                try:
                    yield CatalogSession(blob, lease, decode_catalog(blob.download_blob(lease=lease).readall()))
                finally:
                    lease.release()
        except AzureError as exc:
            raise CatalogError("Document catalog operation failed; check Azure access or lease state.") from exc
