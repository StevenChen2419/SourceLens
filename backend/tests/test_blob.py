from unittest.mock import patch
from urllib.parse import unquote
from uuid import uuid4

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError
from azure.core.exceptions import ResourceNotFoundError

from app.integrations.blob import BlobStore, BlobUploadError


def test_blob_sdk_invocation_and_resource_cleanup() -> None:
    document_id = uuid4()
    filename = "../résumé.pdf"
    with patch("app.integrations.blob.DefaultAzureCredential") as credential_factory, patch(
        "app.integrations.blob.BlobServiceClient"
    ) as client_factory:
        credential = credential_factory.return_value.__enter__.return_value
        client = client_factory.return_value.__enter__.return_value
        store = BlobStore("https://example.blob.core.windows.net", "documents")
        store.upload_pdf(document_id=document_id, filename=filename, data=b"original bytes")

        credential_factory.assert_called_once_with()
        client_factory.assert_called_once_with(account_url=store.account_url, credential=credential)
        client.get_blob_client.assert_called_once_with(container="documents", blob=f"{document_id}.pdf")
        upload = client.get_blob_client.return_value.upload_blob
        upload.assert_called_once()
        assert upload.call_args.args == (b"original bytes",)
        kwargs = upload.call_args.kwargs
        assert kwargs["overwrite"] is False
        assert kwargs["content_settings"].content_type == "application/pdf"
        assert kwargs["metadata"]["document_id"] == str(document_id)
        assert unquote(kwargs["metadata"]["original_filename"]) == filename
        assert kwargs["metadata"]["original_filename"].isascii()
        credential_factory.return_value.__exit__.assert_called_once()
        client_factory.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("error_type", [ClientAuthenticationError, HttpResponseError, ServiceRequestError])
def test_sdk_errors_become_safe_storage_errors(error_type: type[Exception]) -> None:
    with patch("app.integrations.blob.DefaultAzureCredential"), patch(
        "app.integrations.blob.BlobServiceClient"
    ) as factory:
        client = factory.return_value.__enter__.return_value
        client.get_blob_client.return_value.upload_blob.side_effect = error_type("sensitive internals")
        with pytest.raises(BlobUploadError, match="temporarily unavailable") as error:
            BlobStore("https://example.blob.core.windows.net", "documents").upload_pdf(
                document_id=uuid4(), filename="f.pdf", data=b"data"
            )
        assert "sensitive" not in str(error.value)
        assert isinstance(error.value.__cause__, error_type)
        factory.return_value.__exit__.assert_called_once()


def test_missing_configuration_does_not_create_credentials() -> None:
    with patch("app.integrations.blob.DefaultAzureCredential") as credential:
        with pytest.raises(BlobUploadError, match="not configured"):
            BlobStore(None, "documents").upload_pdf(
                document_id=uuid4(), filename="f.pdf", data=b"data"
            )
        credential.assert_not_called()


def test_read_and_delete_use_uuid_blob_only_and_missing_blob_is_idempotent():
    identifier = uuid4()
    with patch("app.integrations.blob.DefaultAzureCredential"), patch("app.integrations.blob.BlobServiceClient") as factory:
        client = factory.return_value.__enter__.return_value
        blob = client.get_blob_client.return_value
        store = BlobStore("https://example.blob.core.windows.net", "documents")
        blob.download_blob.return_value.readall.return_value = b"pdf"
        assert store.read_pdf(identifier) == b"pdf"
        store.delete_pdf(identifier)
        client.get_blob_client.assert_called_with("documents", f"{identifier}.pdf")
        blob.delete_blob.assert_called_once_with()
        blob.download_blob.side_effect = ResourceNotFoundError()
        blob.delete_blob.side_effect = ResourceNotFoundError()
        assert store.read_pdf(identifier) is None
        store.delete_pdf(identifier)


def test_original_deletion_failure_is_not_hidden():
    with patch("app.integrations.blob.DefaultAzureCredential"), patch("app.integrations.blob.BlobServiceClient") as factory:
        factory.return_value.__enter__.return_value.get_blob_client.return_value.delete_blob.side_effect = HttpResponseError("private")
        with pytest.raises(BlobUploadError, match="deletion failed"):
            BlobStore("https://example.blob.core.windows.net", "documents").delete_pdf(uuid4())
