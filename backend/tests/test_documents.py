from collections.abc import Callable, Iterator
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_blob_store, get_settings, get_search_store
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
def client(storage: Mock, search: Mock, embeddings: Mock) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, MAX_UPLOAD_SIZE_MB=1)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_blob_store] = lambda: storage
    app.dependency_overrides[get_search_store] = lambda: search
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


def test_document_ids_are_unique_per_upload(
    client: TestClient, make_pdf: Callable[[list[str]], bytes]
) -> None:
    files = {"file": ("source.pdf", make_pdf(["Text"]), "application/pdf")}
    first = client.post("/api/documents", files=files)
    second = client.post("/api/documents", files=files)
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
