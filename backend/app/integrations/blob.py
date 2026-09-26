"""Keyless storage of original PDFs in an existing private Blob container."""

import logging
from urllib.parse import quote
from uuid import UUID

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

logger = logging.getLogger(__name__)


class BlobUploadError(RuntimeError):
    """An original document could not be stored."""


class BlobStore:
    def __init__(self, account_url: str | None, container: str) -> None:
        self.account_url = account_url
        self.container = container

    def upload_pdf(self, *, document_id: UUID, filename: str, data: bytes) -> None:
        if not self.account_url:
            raise BlobUploadError("Document storage is not configured.")

        try:
            # Context managers close both SDK transports, including on failure.
            with DefaultAzureCredential() as credential:
                with BlobServiceClient(
                    account_url=self.account_url, credential=credential
                ) as client:
                    blob = client.get_blob_client(
                        container=self.container, blob=f"{document_id}.pdf"
                    )
                    blob.upload_blob(
                        data,
                        overwrite=False,
                        content_settings=ContentSettings(content_type="application/pdf"),
                        metadata={
                            "document_id": str(document_id),
                            # Azure metadata headers require ASCII-safe values.
                            "original_filename": quote(filename, safe=""),
                            "filename_encoding": "percent-encoded-utf8",
                        },
                    )
        except AzureError as exc:
            # Do not log exception text: SDK messages can include request details.
            logger.warning("Blob upload failed for document %s (%s)", document_id, type(exc).__name__)
            raise BlobUploadError("Document storage is temporarily unavailable.") from exc
