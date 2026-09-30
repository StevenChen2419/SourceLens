from unittest.mock import patch
from uuid import uuid4

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ResourceExistsError

from app.config import Settings
from app.integrations.catalog import Catalog, CatalogStore, CatalogError, CatalogBusyError, decode_catalog


@pytest.fixture
def store():
    return CatalogStore(Settings(_env_file=None, AZURE_STORAGE_ACCOUNT_URL="https://example.blob.core.windows.net"))


@pytest.fixture
def sdk():
    with patch("app.integrations.catalog.DefaultAzureCredential"), patch("app.integrations.catalog.BlobServiceClient") as factory:
        blob = factory.return_value.__enter__.return_value.get_blob_client.return_value
        blob.download_blob.return_value.readall.return_value = Catalog(initialized=True).model_dump_json().encode()
        yield blob


def test_catalog_scope_is_per_search_service_and_index():
    base = Settings(_env_file=None, AZURE_SEARCH_ENDPOINT="https://one.search.windows.net")
    assert CatalogStore(base).blob_name != CatalogStore(base.model_copy(update={"azure_search_index_name": "knowledgeops-eval-chunks"})).blob_name
    assert CatalogStore(base).blob_name != CatalogStore(base.model_copy(update={"azure_search_endpoint": "https://two.search.windows.net"})).blob_name


def test_existing_catalog_lease_used_for_writes_and_released(store, sdk):
    with store.locked() as session:
        assert session.data.initialized
        session.save()
    sdk.acquire_lease.assert_called_once_with(lease_duration=-1)
    assert sdk.upload_blob.call_args.kwargs["lease"] == sdk.acquire_lease.return_value
    assert sdk.upload_blob.call_args.kwargs["overwrite"] is True
    sdk.acquire_lease.return_value.release.assert_called_once()


def test_create_if_absent_race_never_replaces_existing_catalog(store, sdk):
    sdk.get_blob_properties.side_effect = ResourceNotFoundError()
    sdk.upload_blob.side_effect = ResourceExistsError()
    with store.locked() as session:
        assert session.data.initialized
    assert sdk.upload_blob.call_args.kwargs["overwrite"] is False


def test_lease_conflict_never_yields_a_writable_session(store, sdk):
    error = HttpResponseError("private")
    error.status_code = 409
    sdk.acquire_lease.side_effect = error
    with pytest.raises(CatalogBusyError):
        with store.locked():
            pytest.fail("Lease conflict must stop work")
    sdk.upload_blob.assert_not_called()


def test_lease_released_on_application_failure(store, sdk):
    with pytest.raises(ValueError):
        with store.locked():
            raise ValueError("invalid operation")
    sdk.acquire_lease.return_value.release.assert_called_once()


def test_failed_catalog_write_is_explicit(store, sdk):
    sdk.upload_blob.side_effect = HttpResponseError("sensitive")
    with pytest.raises(CatalogError, match="could not be saved"):
        with store.locked() as session:
            session.save()
    sdk.acquire_lease.return_value.release.assert_called_once()


def test_missing_or_malformed_catalog_is_not_silently_replaced(store, sdk):
    sdk.download_blob.side_effect = ResourceNotFoundError()
    assert store.read().initialized is False
    sdk.download_blob.side_effect = None
    sdk.download_blob.return_value.readall.return_value = b"invalid json"
    with pytest.raises(CatalogError, match="invalid"):
        store.read()
    sdk.upload_blob.assert_not_called()


def test_invalid_catalog_document_keys_rejected():
    import json
    record = {"document_id": str(uuid4()), "filename": "a.pdf", "content_hash": "a" * 64,
              "page_count": 1, "chunk_count": 1, "chunk_ids": ["key"], "state": "indexed"}
    with pytest.raises(CatalogError):
        decode_catalog(json.dumps({"initialized": True, "documents": {"wrong": record}}).encode())


def test_unconfigured_catalog_does_not_attempt_azure():
    with patch("app.integrations.catalog.DefaultAzureCredential") as credentials, pytest.raises(CatalogError):
        CatalogStore(Settings(_env_file=None)).read()
    credentials.assert_not_called()
