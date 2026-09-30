from collections.abc import Callable, Iterator
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_blob_store, get_settings, get_search_store, get_catalog_store
from app.integrations.search import SearchStore, SearchError
from app.integrations.embeddings import EmbeddingAPIError
from app.models import EmbeddedChunk
from app.integrations.blob import BlobStore, BlobUploadError
from app.main import app


@pytest.fixture
def storage() -> Mock:
    return Mock(spec=BlobStore)


@pytest.fixture
def search() -> Mock:
    return Mock(spec=SearchStore)


@pytest.fixture
def embeddings() -> Iterator[Mock]:
    with patch("app.services.documents.embed_chunks") as mock:
        mock.side_effect = lambda chunks, settings: [EmbeddedChunk(
            chunk=chunk, vector=[0.1] * 1536, model="text-embedding-3-small",
            deployment="embedding", dimensions=1536,
        ) for chunk in chunks]
        yield mock


@pytest.fixture
def client(storage: Mock, search: Mock, embeddings: Mock, catalog) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, MAX_UPLOAD_SIZE_MB=1)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_blob_store] = lambda: storage
    app.dependency_overrides[get_search_store] = lambda: search
    app.dependency_overrides[get_catalog_store] = lambda: catalog
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_successful_upload_preserves_original_bytes_and_metadata(
    client: TestClient, storage: Mock, make_pdf: Callable[[list[str]], bytes]
) -> None:
    data = make_pdf(["First page", "", "Third page"])
    filename = "../résumé.pdf"
    response = client.post("/api/documents", files={"file": (filename, data, "application/pdf")})

    assert response.status_code == 201
    body = response.json()
    document_id = UUID(body["document_id"])
    assert document_id.version == 4
    assert body == {
        "document_id": str(document_id), "filename": filename,
        "page_count": 3, "chunk_count": 2,
    }
    storage.upload_pdf.assert_called_once_with(
        document_id=document_id, filename=filename, data=data
    )


def test_upload_counts_multiple_chunks(
    client: TestClient, make_pdf: Callable[[list[str]], bytes]
) -> None:
    response = client.post("/api/documents", files={
        "file": ("long.pdf", make_pdf([" word" * 1100]), "application/pdf")
    })
    assert response.status_code == 201
    assert response.json()["page_count"] == 1
    assert response.json()["chunk_count"] == 3


def test_different_content_with_same_filename_has_distinct_identity(
    client: TestClient, make_pdf: Callable[[list[str]], bytes]
) -> None:
    files = {"file": ("source.pdf", make_pdf(["Text"]), "application/pdf")}
    first = client.post("/api/documents", files=files)
    second = client.post("/api/documents", files={"file": ("source.pdf", make_pdf(["Different text"]), "application/pdf")})
    assert first.status_code == second.status_code == 201
    assert first.json()["document_id"] != second.json()["document_id"]


def test_missing_file(client: TestClient, storage: Mock) -> None:
    assert client.post("/api/documents").status_code == 422
    storage.upload_pdf.assert_not_called()


def test_openapi_declares_one_required_binary_file(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    body = schema["paths"]["/api/documents"]["post"]["requestBody"]
    assert body["required"] is True
    multipart = body["content"]["multipart/form-data"]["schema"]
    component = schema["components"]["schemas"][multipart["$ref"].split("/")[-1]]
    assert "file" in component["required"]
    field = component["properties"]["file"]
    assert field["type"] == "string"
    assert field["format"] == "binary"
    assert "items" not in field


def test_multiple_files_rejected(
    client: TestClient, storage: Mock, make_pdf: Callable[[list[str]], bytes]
) -> None:
    file = ("source.pdf", make_pdf(["Text"]), "application/pdf")
    response = client.post("/api/documents", files=[("file", file), ("file", file)])
    assert response.status_code == 422
    storage.upload_pdf.assert_not_called()


@pytest.mark.parametrize("filename,mime,data", [
    ("notes.txt", "text/plain", b"notes"),
    ("fake.pdf", "application/pdf", b"not a PDF"),
    ("empty.pdf", "application/pdf", b""),
    ("source.pdf", "image/png", b"%PDF-1.7"),
])
def test_non_pdf_upload_rejected(
    client: TestClient, storage: Mock, filename: str, mime: str, data: bytes
) -> None:
    assert client.post("/api/documents", files={"file": (filename, data, mime)}).status_code == 415
    storage.upload_pdf.assert_not_called()


def test_oversized_upload(client: TestClient, storage: Mock) -> None:
    data = b"%PDF-" + b"x" * (1024 * 1024)
    response = client.post("/api/documents", files={"file": ("large.pdf", data, "application/pdf")})
    assert response.status_code == 413
    storage.upload_pdf.assert_not_called()


def test_malformed_pdf(client: TestClient, storage: Mock) -> None:
    response = client.post("/api/documents", files={
        "file": ("broken.pdf", b"%PDF-1.7\nbroken", "application/pdf")
    })
    assert response.status_code == 422
    assert "malformed" in response.json()["detail"]
    storage.upload_pdf.assert_not_called()


def test_no_extractable_text(
    client: TestClient, storage: Mock, make_pdf: Callable[[list[str]], bytes]
) -> None:
    response = client.post("/api/documents", files={
        "file": ("blank.pdf", make_pdf([""]), "application/pdf")
    })
    assert response.status_code == 422
    assert "OCR" in response.json()["detail"]
    storage.upload_pdf.assert_not_called()


def test_storage_failure_is_safe(
    client: TestClient, storage: Mock, make_pdf: Callable[[list[str]], bytes]
) -> None:
    storage.upload_pdf.side_effect = BlobUploadError("SDK secret and internal URL")
    response = client.post("/api/documents", files={
        "file": ("source.pdf", make_pdf(["Text"]), "application/pdf")
    })
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Document storage is unavailable. Please try again later."
    }


def test_bounded_read_rejects_without_storage(storage: Mock, search: Mock) -> None:
    from app.services.documents import UploadTooLargeError, upload_document

    file = Mock()
    file.read.return_value = b"%PDF-1.7 too large"
    with pytest.raises(UploadTooLargeError):
        upload_document(file=file, filename="f.pdf", content_type="application/pdf",
                        max_bytes=10, blob_store=storage, search_store=search, settings=Settings(_env_file=None))
    file.read.assert_called_once_with(11)
    storage.upload_pdf.assert_not_called()


def test_ingestion_sequence_and_indexed_metadata(
    client: TestClient, storage: Mock, search: Mock, embeddings: Mock,
    make_pdf: Callable[[list[str]], bytes],
) -> None:
    calls = Mock()
    calls.attach_mock(search.ensure_index, "ensure")
    calls.attach_mock(embeddings, "embed")
    calls.attach_mock(storage.upload_pdf, "blob")
    calls.attach_mock(search.index_chunks, "index")
    response = client.post("/api/documents", files={"file": ("f.pdf", make_pdf(["First", "", "Third"]), "application/pdf")})
    assert response.status_code == 201
    assert [call[0] for call in calls.mock_calls] == ["ensure", "embed", "blob", "index"]
    indexed = search.index_chunks.call_args.args[0]
    assert [item.chunk.page_number for item in indexed] == [1, 3]
    assert [item.chunk.chunk_index for item in indexed] == [0, 1]
    assert all(item.chunk.document_id == response.json()["document_id"] for item in indexed)
    assert all(item.chunk.filename == "f.pdf" and item.vector == [0.1] * 1536 for item in indexed)


@pytest.mark.parametrize("stage", ["schema", "embedding", "blob", "index"])
def test_ingestion_failure_is_not_success(
    client: TestClient, storage: Mock, search: Mock, embeddings: Mock,
    make_pdf: Callable[[list[str]], bytes], stage: str,
) -> None:
    if stage == "schema":
        search.ensure_index.side_effect = SearchError("private SDK detail")
    elif stage == "embedding":
        embeddings.side_effect = EmbeddingAPIError("private SDK detail")
    elif stage == "blob":
        storage.upload_pdf.side_effect = BlobUploadError("private SDK detail")
    else:
        search.index_chunks.side_effect = SearchError("private SDK detail")
    response = client.post("/api/documents", files={"file": ("f.pdf", make_pdf(["Text"]), "application/pdf")})
    assert response.status_code == 503
    assert "private SDK detail" not in response.text
    if stage in ("schema", "embedding"):
        storage.upload_pdf.assert_not_called()
    if stage != "index":
        search.index_chunks.assert_not_called()
    else:
        assert response.json()["detail"]["document_id"] == str(storage.upload_pdf.call_args.kwargs["document_id"])


@pytest.mark.parametrize("second_filename", ["first.pdf", "renamed.pdf"])
def test_identical_bytes_return_existing_identity_without_azure_writes(client, embeddings, storage, search, make_pdf, second_filename):
    data = make_pdf(["Identical PDF"])
    first = client.post("/api/documents", files={"file": ("first.pdf", data, "application/pdf")})
    duplicate = client.post("/api/documents", files={"file": (second_filename, data, "application/pdf")})
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {
        "code": "duplicate_document", "document_id": first.json()["document_id"],
        "filename": "first.pdf", "state": "indexed",
        "message": "Identical PDF already registered. No new chunks were indexed.",
    }
    embeddings.assert_called_once()
    storage.upload_pdf.assert_called_once()
    search.index_chunks.assert_called_once()


def test_listing_contains_metadata_and_states_not_internal_keys(client, catalog, make_pdf):
    assert client.get("/api/documents").json() == {"documents": []}
    result = client.post("/api/documents", files={"file": ("source.pdf", make_pdf(["one", "", "three"]), "application/pdf")})
    response = client.get("/api/documents")
    assert response.status_code == 200
    assert response.json()["documents"] == [result.json() | {"state": "indexed", "original_retained_on_delete": False}]
    assert "content_hash" not in response.text and "chunk_ids" not in response.text
    catalog.data.documents[result.json()["document_id"]].state = "indexing"
    assert client.get("/api/documents").json()["documents"][0]["state"] == "indexing"


def test_failed_ingestion_is_listed_and_reserves_hash(client, catalog, search, embeddings, make_pdf):
    data = make_pdf(["partial"])
    search.index_chunks.side_effect = SearchError("partial indexing")
    assert client.post("/api/documents", files={"file": ("a.pdf", data, "application/pdf")}).status_code == 503
    listing = client.get("/api/documents").json()["documents"]
    assert len(listing) == 1 and listing[0]["state"] == "failed"
    response = client.post("/api/documents", files={"file": ("b.pdf", data, "application/pdf")})
    assert response.status_code == 409 and response.json()["detail"]["state"] == "failed"
    embeddings.assert_called_once()


def test_deletion_search_before_blob_then_hides_document_and_is_retryable(client, catalog, search, storage, make_pdf):
    result = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(["one", "two"]), "application/pdf")}).json()
    identifier = result["document_id"]
    operations = Mock()
    operations.attach_mock(search.delete_document_chunks, "search")
    operations.attach_mock(storage.delete_pdf, "blob")
    response = client.delete(f"/api/documents/{identifier}")
    assert response.status_code == 200
    assert response.json() == {"document_id": identifier, "status": "deleted", "original_retained": False}
    assert [call[0] for call in operations.mock_calls] == ["search", "blob"]
    assert search.delete_document_chunks.call_args.args == (UUID(identifier), catalog.data.documents[identifier].chunk_ids)
    assert len(catalog.data.documents[identifier].chunk_ids) == 2
    assert client.get("/api/documents").json() == {"documents": []}
    assert client.delete(f"/api/documents/{identifier}").status_code == 200
    assert search.delete_document_chunks.call_count == 2


@pytest.mark.parametrize("failed_stage", ["search", "blob"])
def test_partial_delete_remains_visible_until_successful_retry(client, catalog, search, storage, make_pdf, failed_stage):
    result = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(["one"]), "application/pdf")}).json()
    operation = search.delete_document_chunks if failed_stage == "search" else storage.delete_pdf
    operation.side_effect = SearchError("sensitive") if failed_stage == "search" else BlobUploadError("sensitive")
    response = client.delete(f"/api/documents/{result['document_id']}")
    assert response.status_code == 503 and "sensitive" not in response.text
    assert client.get("/api/documents").json()["documents"][0]["state"] == "deleting"
    if failed_stage == "search":
        storage.delete_pdf.assert_not_called()
    operation.side_effect = None
    assert client.delete(f"/api/documents/{result['document_id']}").status_code == 200
    assert client.get("/api/documents").json() == {"documents": []}


def test_hash_is_released_only_after_successful_deletion(client, search, make_pdf):
    data = make_pdf(["reupload"])
    files = {"file": ("a.pdf", data, "application/pdf")}
    first = client.post("/api/documents", files=files).json()
    search.delete_document_chunks.side_effect = SearchError("still visible")
    assert client.delete(f"/api/documents/{first['document_id']}").status_code == 503
    assert client.post("/api/documents", files=files).status_code == 409
    search.delete_document_chunks.side_effect = None
    assert client.delete(f"/api/documents/{first['document_id']}").status_code == 200
    second = client.post("/api/documents", files=files)
    assert second.status_code == 201
    assert second.json()["document_id"] != first["document_id"]


def test_unknown_and_invalid_ids_do_not_delete(client, search, storage):
    from uuid import uuid4
    assert client.delete(f"/api/documents/{uuid4()}").status_code == 404
    assert client.delete("/api/documents/not-a-uuid").status_code == 422
    search.delete_document_chunks.assert_not_called()
    storage.delete_pdf.assert_not_called()


def test_catalog_conflict_blocks_upload_and_delete(client, catalog, embeddings, search, storage, make_pdf):
    from uuid import uuid4
    catalog.busy = True
    assert client.post("/api/documents", files={"file": ("a.pdf", make_pdf(["busy"]), "application/pdf")}).status_code == 409
    assert client.delete(f"/api/documents/{uuid4()}").status_code == 409
    embeddings.assert_not_called()
    storage.upload_pdf.assert_not_called()
    search.delete_document_chunks.assert_not_called()


def test_catalog_reservation_failure_prevents_ingestion(client, catalog, embeddings, storage, make_pdf):
    from app.integrations.catalog import CatalogError
    catalog.save_error = CatalogError("unavailable")
    response = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(["reserved"]), "application/pdf")})
    assert response.status_code == 503
    embeddings.assert_not_called()
    storage.upload_pdf.assert_not_called()


def test_final_catalog_save_failure_does_not_claim_success(client, catalog, embeddings, make_pdf):
    from app.integrations.catalog import CatalogError
    original = embeddings.side_effect
    def embed_then_fail_save(chunks, settings):
        catalog.save_error = CatalogError("final save failed")
        return original(chunks, settings)
    embeddings.side_effect = embed_then_fail_save
    response = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(["saved"]), "application/pdf")})
    assert response.status_code == 503
    assert next(iter(catalog.data.documents.values())).state == "indexing"


def test_delete_final_save_failure_remains_retryable(client, catalog, storage, make_pdf):
    from app.integrations.catalog import CatalogError
    result = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(["delete save"]), "application/pdf")}).json()
    identifier = result["document_id"]
    def lose_catalog_write(_document_id):
        catalog.save_error = CatalogError("unavailable")
    storage.delete_pdf.side_effect = lose_catalog_write
    assert client.delete(f"/api/documents/{identifier}").status_code == 503
    assert catalog.data.documents[identifier].state == "deleting"
    catalog.save_error = None
    storage.delete_pdf.side_effect = None
    assert client.delete(f"/api/documents/{identifier}").status_code == 200


def test_legacy_metadata_failure_is_explicit_not_an_empty_catalog(client, catalog, search):
    catalog.data.initialized = False
    search.document_chunks.return_value = [{"document_id": "not-a-pdf-uuid"}]
    assert client.get("/api/documents").status_code == 503
    assert not catalog.data.initialized
