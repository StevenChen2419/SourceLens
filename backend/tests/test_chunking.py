from collections.abc import Callable

import pytest
import tiktoken
from pydantic import ValidationError

from app.models import DocumentChunk, PDFPage
from app.services.chunking import TOKEN_ENCODING, ChunkingOptions, chunk_pages
from app.services.pdf import extract_pdf_pages


def test_pdf_to_chunks_preserves_metadata_and_page_boundaries(
    make_pdf: Callable[[list[str]], bytes],
) -> None:
    pages = extract_pdf_pages(make_pdf(["First page content", "", "Third page content"]))
    chunks = chunk_pages(pages, document_id="doc-123", filename="handbook.pdf")

    assert all(isinstance(chunk, DocumentChunk) for chunk in chunks)
    assert [chunk.page_number for chunk in chunks] == [1, 3]
    assert [chunk.text for chunk in chunks] == ["First page content", "Third page content"]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert all(chunk.document_id == "doc-123" for chunk in chunks)
    assert all(chunk.filename == "handbook.pdf" for chunk in chunks)


def test_default_token_limit_and_overlap() -> None:
    encoding = tiktoken.get_encoding(TOKEN_ENCODING)
    text = " word" * 1100
    chunks = chunk_pages([PDFPage(page_number=1, text=text)], document_id="d", filename="f")
    tokens = [encoding.encode_ordinary(chunk.text) for chunk in chunks]

    assert [len(window) for window in tokens] == [500, 500, 250]
    assert tokens[0][-75:] == tokens[1][:75]
    assert tokens[1][-75:] == tokens[2][:75]
    reconstructed = chunks[0].text + "".join(
        encoding.decode(window[75:]) for window in tokens[1:]
    )
    assert reconstructed == text


def test_configurable_overlap_uses_source_tokens() -> None:
    encoding = tiktoken.get_encoding(TOKEN_ENCODING)
    text = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu " * 8
    source = encoding.encode_ordinary(text)
    chunks = chunk_pages(
        [PDFPage(page_number=1, text=text)],
        document_id="d", filename="f",
        options=ChunkingOptions(max_tokens=20, overlap_tokens=5),
    )
    start = 0
    for chunk in chunks:
        assert chunk.text == encoding.decode(source[start:start + 20])
        assert len(encoding.encode_ordinary(chunk.text)) <= 20
        start += 15


@pytest.mark.parametrize(
    "text",
    ["😀汉字 café e\u0301 " * 30, "<|endoftext|> data " * 20, "Start" + "\n " * 50 + "End"],
    ids=["unicode", "special-token-text", "whitespace-between-text"],
)
def test_zero_overlap_preserves_unicode_and_special_token_text(text: str) -> None:
    encoding = tiktoken.get_encoding(TOKEN_ENCODING)
    chunks = chunk_pages(
        [PDFPage(page_number=1, text=text)],
        document_id="d", filename="f",
        options=ChunkingOptions(max_tokens=9, overlap_tokens=0),
    )

    assert "".join(chunk.text for chunk in chunks) == text
    assert all(len(encoding.encode_ordinary(chunk.text)) <= 9 for chunk in chunks)


def test_empty_pages_produce_no_chunks() -> None:
    pages = [PDFPage(page_number=1, text=""), PDFPage(page_number=2, text=" \n ")]
    assert chunk_pages(pages, document_id="d", filename="f") == []
    assert chunk_pages([], document_id="d", filename="f") == []


@pytest.mark.parametrize("overlap", [1, 5, 8])
def test_unicode_overlap_does_not_drop_text(overlap: int) -> None:
    text = "😀Start 汉字 café e\u0301 End 🦄 finish 東京 next à bientôt"
    chunks = chunk_pages(
        [PDFPage(page_number=1, text=text)], document_id="d", filename="f",
        options=ChunkingOptions(max_tokens=9, overlap_tokens=overlap),
    )
    covered = 0
    previous_start = -1
    encoding = tiktoken.get_encoding(TOKEN_ENCODING)
    for chunk in chunks:
        start = text.index(chunk.text, previous_start + 1)
        assert start <= covered
        covered = max(covered, start + len(chunk.text))
        previous_start = start
        assert len(encoding.encode_ordinary(chunk.text)) <= 9
    assert covered == len(text)


def test_ids_are_deterministic_unique_and_document_specific() -> None:
    pages = [PDFPage(page_number=number, text="Same text") for number in (1, 2)]
    first = chunk_pages(pages, document_id="d", filename="f")
    repeated = chunk_pages(pages, document_id="d", filename="f")
    other = chunk_pages(pages, document_id="other", filename="f")
    changed = chunk_pages([PDFPage(page_number=1, text="Changed")], document_id="d", filename="f")

    assert first == repeated
    assert len({chunk.chunk_id for chunk in first}) == len(first)
    assert {chunk.chunk_id for chunk in first}.isdisjoint(chunk.chunk_id for chunk in other)
    assert changed[0].chunk_id != first[0].chunk_id


@pytest.mark.parametrize("size,overlap", [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 11)])
def test_invalid_chunking_options(size: int, overlap: int) -> None:
    with pytest.raises(ValidationError):
        ChunkingOptions(max_tokens=size, overlap_tokens=overlap)


def test_tiny_token_limit_fails_instead_of_corrupting_unicode() -> None:
    with pytest.raises(ValueError, match="Unicode"):
        chunk_pages(
            [PDFPage(page_number=1, text="😀")], document_id="d", filename="f",
            options=ChunkingOptions(max_tokens=1, overlap_tokens=0),
        )


def test_blank_document_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="document_id"):
        chunk_pages([], document_id=" ", filename="f")
