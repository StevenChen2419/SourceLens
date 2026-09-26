"""Extract text locally without OCR or document storage."""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.models import PDFPage


class MalformedPDFError(ValueError):
    """The PDF cannot be parsed or its text cannot be read."""


class UnsupportedPDFError(ValueError):
    """The PDF requires capabilities this application does not provide."""


class NoExtractableTextError(UnsupportedPDFError):
    """No page contains extractable, non-whitespace text."""


def extract_pdf_pages(pdf_bytes: bytes) -> list[PDFPage]:
    """Return every page, including empty pages, with its original page number.

    Strict parsing rejects malformed PDFs rather than silently repairing them.
    Encrypted PDFs are unsupported, including those with an empty password.
    """
    try:
        reader = PdfReader(BytesIO(pdf_bytes), strict=True)
        if reader.is_encrypted:
            raise UnsupportedPDFError("Encrypted PDFs are not supported.")

        pages = [
            PDFPage(page_number=number, text=(page.extract_text() or "").strip())
            for number, page in enumerate(reader.pages, start=1)
        ]
    except UnsupportedPDFError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError) as exc:
        raise MalformedPDFError(
            "The PDF is malformed or its text could not be extracted."
        ) from exc

    if not any(page.text for page in pages):
        raise NoExtractableTextError(
            "This PDF has no extractable text and is unsupported. "
            "Scanned/image-only PDFs require OCR, which is not implemented."
        )
    return pages
