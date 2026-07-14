# app/services/image_validation.py
from __future__ import annotations

import io

import magic
from fastapi import HTTPException, status
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings as app_settings

MAX_IMAGE_BYTES = app_settings.MAX_UPLOAD_MB * 1024 * 1024

# Map allowed MIME types to the Pillow format name and the format we
# re-encode to. Re-encoding always to the SAME detected format (not
# converting jpeg->png etc.) keeps behavior predictable for callers.
_ALLOWED = {
    "image/jpeg": ("JPEG", "image/jpeg"),
    "image/png": ("PNG", "image/png"),
    "image/webp": ("WEBP", "image/webp"),
}


def validate_and_clean_image(raw_bytes: bytes) -> tuple[bytes, str]:
    """Validates an uploaded image and returns (clean_bytes, content_type).

    Defense layers, in order:
    1. Size check.
    2. Content-based MIME sniff via libmagic (ignores whatever Content-Type
       header the client claims -- that header is attacker-controlled).
    3. Pillow structural verification -- rejects corrupt / non-image data
       that happens to start with a valid-looking magic number.
    4. Re-encode via Pillow and return NEW bytes -- this is what actually
       strips any payload appended after the real image data (polyglot
       files: valid JPEG/PNG header + trailing HTML/JS/PHP). The caller
       must upload the returned clean_bytes, NOT the original raw_bytes.

    Raises HTTPException(400) on any failure.
    """
    if len(raw_bytes) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    if len(raw_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Image too large, max {app_settings.MAX_UPLOAD_MB}MB",
        )

    # 2. Content-based MIME sniff -- never trust file.content_type from the client.
    detected_mime = magic.from_buffer(raw_bytes, mime=True)
    if detected_mime not in _ALLOWED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only JPEG, PNG, or WEBP images are supported",
        )

    pillow_format, content_type = _ALLOWED[detected_mime]

    # 3. Structural verification.
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.verify()  # raises if the image data is corrupt/incomplete
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is not a valid image")

    # 4. Re-encode -- verify() leaves the file object unusable for further
    # ops, so re-open fresh. Re-saving produces bytes containing ONLY the
    # actual decoded pixel data in our chosen format: this is what strips
    # any trailing/appended payload a polyglot file might carry.
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()  # force full decode now, while we still control errors
            img = ImageOps.exif_transpose(img)  # bake EXIF rotation into pixels before we drop the tag
            # Flatten to RGB for JPEG (no alpha channel support); keep RGBA
            # for PNG/WEBP where transparency is meaningful.
            if pillow_format == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            out = io.BytesIO()
            save_kwargs = {"format": pillow_format}
            if pillow_format == "JPEG":
                save_kwargs["quality"] = 90
            img.save(out, **save_kwargs)
            clean_bytes = out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Failed to process image") from exc

    return clean_bytes, content_type