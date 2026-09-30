from collections.abc import Callable
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas
from contextlib import contextmanager
from unittest.mock import Mock
from app.integrations.catalog import Catalog, CatalogBusyError


class MemoryCatalog:
    """Offline durable-copy/lease semantics for lifecycle service tests."""
    def __init__(self):
        self.data = Catalog(initialized=True)
        self.busy = False
        self.save_error = None

    def read(self):
        return self.data.model_copy(deep=True)

    @contextmanager
    def locked(self):
        if self.busy:
            raise CatalogBusyError("Busy")
        self.busy = True
        session = Mock()
        session.data = self.read()
        def save():
            if self.save_error:
                raise self.save_error
            self.data = session.data.model_copy(deep=True)
        session.save.side_effect = save
        try:
            yield session
        finally:
            self.busy = False


@pytest.fixture
def catalog():
    return MemoryCatalog()


@pytest.fixture
def make_pdf() -> Callable[[list[str]], bytes]:
    """Generate real, small PDF inputs in memory, with one string per page."""
    def build(pages: list[str]) -> bytes:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(612, 792))
        for text in pages:
            if text:
                pdf.drawString(72, 720, text)
            pdf.showPage()
        pdf.save()
        return buffer.getvalue()

    return build
