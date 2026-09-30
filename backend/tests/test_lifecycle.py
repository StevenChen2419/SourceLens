from io import BytesIO
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from app.config import Settings
from app.integrations.blob import BlobStore
from app.integrations.catalog import CatalogError, CatalogBusyError
from app.integrations.search import SearchStore
from app.services.documents import prepare_upload
from app.services.lifecycle import list_documents, upload_managed_document, delete_document, DuplicateDocumentError


@pytest.fixture
def legacy(make_pdf, catalog):
    data = make_pdf(["First", "", "Third"])
    prepared = prepare_upload(file=BytesIO(data), filename="legacy.pdf", content_type="application/pdf", max_bytes=len(data))
    blob = Mock(spec=BlobStore)
    blob.read_pdf.return_value = data
    search = Mock(spec=SearchStore)
    search.document_chunks.return_value = [chunk.model_dump(exclude={"text"}) for chunk in prepared.chunks]
    catalog.data.initialized = False
    return data, prepared, blob, search


def test_adopts_existing_identity_hash_counts_and_retains_legacy_original(legacy, catalog):
    data, prepared, blob, search = legacy
    listing = list_documents(catalog, blob, search)
    assert len(listing) == 1
    assert listing[0].document_id == prepared.document_id
    assert listing[0].page_count == 3 and listing[0].chunk_count == 2
    assert listing[0].state == "indexed" and listing[0].original_retained_on_delete
    with patch("app.services.documents.embed_chunks") as embeddings, pytest.raises(DuplicateDocumentError):
        upload_managed_document(file=BytesIO(data), filename="different.pdf", content_type="application/pdf",
                                max_bytes=len(data), blob_store=blob, search_store=search,
                                settings=Settings(_env_file=None), catalog=catalog)
    embeddings.assert_not_called()
    response = delete_document(prepared.document_id, catalog, blob, search)
    assert response.original_retained
    blob.delete_pdf.assert_not_called()
    blob.upload_pdf.assert_not_called()
    search.index_chunks.assert_not_called()


def test_legacy_duplicates_remain_separate_and_all_reserve_same_hash(legacy, catalog):
    data, prepared, blob, search = legacy
    other = prepare_upload(file=BytesIO(data), filename="renamed.pdf", content_type="application/pdf", max_bytes=len(data))
    search.document_chunks.return_value += [chunk.model_dump(exclude={"text"}) for chunk in other.chunks]
    assert len(list_documents(catalog, blob, search)) == 2
    delete_document(prepared.document_id, catalog, blob, search)
    with pytest.raises(DuplicateDocumentError) as error:
        upload_managed_document(file=BytesIO(data), filename="new.pdf", content_type="application/pdf", max_bytes=len(data),
                                blob_store=blob, search_store=search, settings=Settings(_env_file=None), catalog=catalog)
    assert error.value.record.document_id == other.document_id


@pytest.mark.parametrize("problem", ["missing_chunk", "missing_original", "malformed_original", "incorrect_metadata"])
def test_incomplete_legacy_is_visible_and_deletable(legacy, catalog, problem):
    _, prepared, blob, search = legacy
    if problem == "missing_chunk":
        search.document_chunks.return_value.pop()
    elif problem == "missing_original":
        blob.read_pdf.return_value = None
    elif problem == "malformed_original":
        blob.read_pdf.return_value = b"broken"
    else:
        search.document_chunks.return_value[0]["page_number"] = 9
    listing = list_documents(catalog, blob, search)
    assert listing[0].state == "failed"
    assert delete_document(prepared.document_id, catalog, blob, search).original_retained


def test_unknown_legacy_hash_fails_closed_for_new_upload(legacy, catalog):
    data, _, blob, search = legacy
    blob.read_pdf.return_value = None
    with pytest.raises(CatalogError, match="missing"):
        upload_managed_document(file=BytesIO(data), filename="new.pdf", content_type="application/pdf", max_bytes=len(data),
                                blob_store=blob, search_store=search, settings=Settings(_env_file=None), catalog=catalog)
    search.index_chunks.assert_not_called()


def test_only_first_listing_adopts_legacy(legacy, catalog):
    _, _, blob, search = legacy
    list_documents(catalog, blob, search)
    list_documents(catalog, blob, search)
    search.document_chunks.assert_called_once()
    blob.read_pdf.assert_called_once()


def test_catalog_lease_excludes_a_second_ingestion(catalog, make_pdf):
    blob, search = Mock(spec=BlobStore), Mock(spec=SearchStore)
    data = make_pdf(["Concurrent"])
    with catalog.locked(), patch("app.services.documents.embed_chunks") as embeddings:
        with pytest.raises(CatalogBusyError):
            upload_managed_document(file=BytesIO(data), filename="a.pdf", content_type="application/pdf", max_bytes=len(data),
                                    blob_store=blob, search_store=search, settings=Settings(_env_file=None), catalog=catalog)
    embeddings.assert_not_called()
    search.index_chunks.assert_not_called()
