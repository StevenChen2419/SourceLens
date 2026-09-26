from collections.abc import Callable
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas


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
