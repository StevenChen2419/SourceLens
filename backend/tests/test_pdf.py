from base64 import b64decode
from collections.abc import Callable
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.services.pdf import (
    MalformedPDFError,
    NoExtractableTextError,
    UnsupportedPDFError,
    extract_pdf_pages,
)


def test_single_page_pdf(make_pdf: Callable[[list[str]], bytes]) -> None:
    pages = extract_pdf_pages(make_pdf(["KnowledgeOps source text."]))

    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].text == "KnowledgeOps source text."


def test_multiple_pages_preserve_empty_pages_and_numbers(
    make_pdf: Callable[[list[str]], bytes],
) -> None:
    pages = extract_pdf_pages(make_pdf(["First page", "", "Third page"]))

    assert [page.page_number for page in pages] == [1, 2, 3]
    assert [page.text for page in pages] == ["First page", "", "Third page"]


@pytest.mark.parametrize("text", ["", "   "])
def test_pdf_without_text_is_unsupported(
    make_pdf: Callable[[list[str]], bytes], text: str
) -> None:
    with pytest.raises(NoExtractableTextError, match="OCR"):
        extract_pdf_pages(make_pdf([text, ""]))


def test_image_only_pdf_is_unsupported() -> None:
    # A real embedded image, with no hidden text layer to extract.
    image = ImageReader(BytesIO(b64decode(
        "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    )))
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawImage(image, 72, 72, width=100, height=100)
    pdf.save()

    with pytest.raises(NoExtractableTextError, match="OCR"):
        extract_pdf_pages(buffer.getvalue())


@pytest.mark.parametrize("data", [b"", b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_malformed_pdf_has_clear_error(data: bytes) -> None:
    with pytest.raises(MalformedPDFError, match="malformed") as error:
        extract_pdf_pages(data)
    assert error.value.__cause__ is not None


def test_truncated_real_pdf_is_rejected(
    make_pdf: Callable[[list[str]], bytes],
) -> None:
    data = make_pdf(["Text in a damaged PDF"])
    with pytest.raises(MalformedPDFError):
        extract_pdf_pages(data[: len(data) // 2])


def test_encrypted_pdf_is_unsupported(
    make_pdf: Callable[[list[str]], bytes],
) -> None:
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(make_pdf(["Private text"]))))
    writer.encrypt("test-password")
    buffer = BytesIO()
    writer.write(buffer)

    with pytest.raises(UnsupportedPDFError, match="Encrypted"):
        extract_pdf_pages(buffer.getvalue())
