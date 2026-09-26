"""Application dependencies, replaceable in tests without Azure access."""

from typing import Annotated

from fastapi import Depends

from app.config import Settings
from app.integrations.blob import BlobStore


def get_settings() -> Settings:
    return Settings()


def get_blob_store(settings: Annotated[Settings, Depends(get_settings)]) -> BlobStore:
    # SDK clients and credentials are created only when an upload is attempted.
    return BlobStore(settings.azure_storage_account_url, settings.azure_storage_container)
