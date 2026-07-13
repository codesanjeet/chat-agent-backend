from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import anyio
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Env ───────────────────────────────────────────────────────────────────────

R2_BUCKET: str = os.getenv("R2_BUCKET_NAME", "kiteum-agent")
R2_PUBLIC_BASE: str = os.getenv("R2_PUBLIC_BASE", "").rstrip("/")
_R2_ENDPOINT: str = os.getenv("R2_ENDPOINT", "")
_R2_ACCESS_KEY: str = os.getenv("R2_ACCESS_KEY", "")
_R2_SECRET_KEY: str = os.getenv("R2_SECRET_KEY", "")

_MISSING = [
    name for name, val in {
        "R2_BUCKET_NAME": R2_BUCKET,
        "R2_PUBLIC_BASE": R2_PUBLIC_BASE,
        "R2_ENDPOINT": _R2_ENDPOINT,
        "R2_ACCESS_KEY": _R2_ACCESS_KEY,
        "R2_SECRET_KEY": _R2_SECRET_KEY,
    }.items()
    if not val
]
if _MISSING:
    raise RuntimeError(
        f"Missing required R2 env vars: {', '.join(_MISSING)}\n"
        "Set them in your .env before starting the application."
    )


class R2StorageError(RuntimeError):
    """Raised when an R2 operation fails in a way the caller must handle."""


class R2Storage:
    """
    Async-safe Cloudflare R2 client for the single public `kiteum-agent` bucket.

    A new boto3 client is created inside each method call because boto3
    clients are NOT thread-safe across threads -- one per call is the correct
    pattern for anyio thread-pool usage.

    Use the module-level singleton `r2`. Do not instantiate this class yourself.
    """

    @staticmethod
    def _client():
        return boto3.client(
            service_name="s3",
            endpoint_url=_R2_ENDPOINT,
            aws_access_key_id=_R2_ACCESS_KEY,
            aws_secret_access_key=_R2_SECRET_KEY,
            region_name="auto",
        )

    # ── Key builders ──────────────────────────────────────────────────────────

    @staticmethod
    def build_key(tenant_id: str, folder: str, filename: str) -> str:
        """Generic key for non-gallery uploads (avatars, notifications, etc).
        Layout: businesses/<tenant_id>/<folder>/<uuid><ext>
        """
        ext = Path(filename).suffix.lower()
        return f"businesses/{tenant_id}/{folder}/{uuid.uuid4().hex}{ext}"

    @staticmethod
    def build_gallery_key(tenant_id: uuid.UUID, parent_id: uuid.UUID | None, filename: str) -> str:
        """
        Gallery-specific key. Mirrors the folder/image tree in R2 itself:

            businesses/<tenant_id>/gallery/<uuid><ext>                     (root-level image)
            businesses/<tenant_id>/gallery/<parent_id>/<uuid><ext>         (image inside a folder)

        This isn't required for the app to function (the DB tree is what's
        authoritative) but it keeps the bucket human-browsable, and makes it
        obvious in R2 which objects belong to which property/folder.
        """
        ext = Path(filename).suffix.lower()
        if parent_id:
            return f"businesses/{tenant_id}/gallery/{parent_id}/{uuid.uuid4().hex}{ext}"
        return f"businesses/{tenant_id}/gallery/{uuid.uuid4().hex}{ext}"

    # ── Upload / Delete ───────────────────────────────────────────────────────

    async def upload(self, key: str, content: bytes, content_type: str) -> str:
        """Upload bytes to the public bucket. Returns the key. Raises R2StorageError on failure."""

        def _do() -> None:
            self._client().put_object(
                Bucket=R2_BUCKET,
                Key=key,
                Body=content,
                ContentType=content_type,
                CacheControl="public, max-age=31536000",
            )

        try:
            await anyio.to_thread.run_sync(_do)
            logger.info("[R2] uploaded '%s'", key)
            return key
        except (BotoCoreError, ClientError) as exc:
            logger.error("[R2] upload failed for '%s': %s", key, exc)
            raise R2StorageError(f"Upload failed: {exc}") from exc

    async def delete(self, key: str | None) -> None:
        """Delete an object. Silently skips None/empty keys. Logs a warning on failure but never raises."""
        if not key:
            return

        def _do() -> None:
            self._client().delete_object(Bucket=R2_BUCKET, Key=key)

        try:
            await anyio.to_thread.run_sync(_do)
            logger.info("[R2] deleted '%s'", key)
        except (BotoCoreError, ClientError) as exc:
            logger.warning("[R2] delete warning for '%s': %s", key, exc)

    # ── URLs / metadata ───────────────────────────────────────────────────────

    def public_url(self, key: str) -> str:
        """Permanent CDN URL. No network call."""
        return f"{R2_PUBLIC_BASE}/{key}"

    async def head(self, key: str) -> dict:
        """Return {"size": int, "content_type": str}. Returns {} on failure -- never raises."""

        def _do() -> dict:
            return self._client().head_object(Bucket=R2_BUCKET, Key=key)

        try:
            resp = await anyio.to_thread.run_sync(_do)
            return {
                "size": resp.get("ContentLength", 0),
                "content_type": resp.get("ContentType", "application/octet-stream"),
            }
        except (BotoCoreError, ClientError) as exc:
            logger.warning("[R2] head failed for '%s': %s", key, exc)
            return {}

    async def size(self, key: str) -> int:
        return (await self.head(key)).get("size", 0)


r2 = R2Storage()