"""Application dependencies, replaceable in tests without Azure access."""

from typing import Annotated

from fastapi import Depends

from app.config import Settings
from app.access import DemoSearchStore
from app.integrations.blob import BlobStore
from app.integrations.search import SearchStore
from app.integrations.catalog import CatalogStore


def get_settings() -> Settings:
    return Settings()


def get_blob_store(settings: Annotated[Settings, Depends(get_settings)]) -> BlobStore:
    # SDK clients and credentials are created only when an upload is attempted.
    return BlobStore(settings.azure_storage_account_url, settings.azure_storage_container)


def get_search_store(settings: Annotated[Settings, Depends(get_settings)]) -> SearchStore:
    return DemoSearchStore(settings) if settings.app_mode == "public_demo" else SearchStore(settings)


def get_catalog_store(settings: Annotated[Settings, Depends(get_settings)]) -> CatalogStore:
    return CatalogStore(settings)
