from __future__ import annotations

import io

from fastapi import HTTPException, status
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.config import settings as app_settings

MAX_PDF_BYTES = app_settings.MAX_UPLOAD_MB * 1024 * 1024
MAX_PDF_PAGES = 5


def validate_pdf(raw_bytes: bytes) -> bytes:
    """Validates an uploaded PDF and returns the same bytes back if it passes.

    Defense layers, in order:
    1. Size check.
    2. Structural parse via pypdf -- rejects non-PDF / corrupt data that
       happens to pass a client-supplied content-type check.
    3. Page-count cap -- each page can turn into an LLM extraction call
       downstream, so this is the main cost control, not just a sanity check.

    Raises HTTPException(400) on any failure.
    """
    if len(raw_bytes) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    if len(raw_bytes) > MAX_PDF_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"PDF too large, max {app_settings.MAX_UPLOAD_MB}MB",
        )

    try:
        reader = PdfReader(io.BytesIO(raw_bytes), strict=True)
        page_count = len(reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is not a valid PDF") from exc

    if page_count == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "PDF has no pages")

    if page_count > MAX_PDF_PAGES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"PDF has too many pages, max {MAX_PDF_PAGES}",
        )

    return raw_bytes