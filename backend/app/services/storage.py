"""Media storage.

An abstraction with one deliberate reason: Buffer downloads media over the
public internet, so uploaded files must live somewhere publicly reachable.
Local disk works for a preview or an on-premise setup; S3/R2/Cloudflare R2 is
the real deployment. Both satisfy the same interface, so swapping is a
configuration change, not a rewrite.

Security rules enforced here:
  * filenames are generated, never derived from user input (no path traversal)
  * content is validated by magic bytes, not by extension or the browser's
    claimed MIME type
  * files are served with a locked-down content type and nosniff
"""
from __future__ import annotations

import secrets
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.config import Settings
from app.core.logging import get_logger
from app.services import media_signing


logger = get_logger("app.media")

#: Enough bytes to cover every signature we inspect.
_HEAD_SNIFF_BYTES = 32

#: Objects at or below this go up in a single PUT. Above it, multipart.
_S3_SINGLE_PUT_LIMIT = 64 * 1024 * 1024

#: Part size for a multipart object. S3 requires at least 5 MiB for every part
#: except the last.
_S3_PART_SIZE = 16 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Magic-byte sniffing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MediaKind:
    kind: str          # "image" | "video"
    mime: str
    extension: str


# (offset, signature) -> MediaKind
_SIGNATURES: list[tuple[int, bytes, MediaKind]] = [
    (0, b"\xff\xd8\xff", MediaKind("image", "image/jpeg", "jpg")),
    (0, b"\x89PNG\r\n\x1a\n", MediaKind("image", "image/png", "png")),
    (0, b"GIF87a", MediaKind("image", "image/gif", "gif")),
    (0, b"GIF89a", MediaKind("image", "image/gif", "gif")),
    (0, b"BM", MediaKind("image", "image/bmp", "bmp")),
    (8, b"WEBP", MediaKind("image", "image/webp", "webp")),
    (4, b"ftyp", MediaKind("video", "video/mp4", "mp4")),
    (4, b"moov", MediaKind("video", "video/mp4", "mp4")),
    (4, b"mdat", MediaKind("video", "video/mp4", "mp4")),
]


def sniff(head: bytes) -> MediaKind | None:
    """Identify a file from its magic bytes."""
    for offset, signature, kind in _SIGNATURES:
        if head[offset : offset + len(signature)] == signature:
            return kind
    return None


class UnsupportedMedia(ValueError):
    """Raised when a file is not a recognised image or video."""


class MediaTooLarge(ValueError):
    """Raised when a file exceeds the configured limit."""


def _remaining(stream: BinaryIO) -> int:
    """Bytes left in a stream, or 0 when that cannot be determined cheaply.

    A file opened from disk reports its real size, which is what lets S3 pick
    between a single PUT and a multipart upload. An upload socket does not, and
    0 there means "assume it is large", which is the safe direction.
    """
    try:
        current = stream.tell()
        stream.seek(0, 2)
        end = stream.tell()
        stream.seek(current)
        return max(0, end - current)
    except (AttributeError, OSError, ValueError):
        return 0


class _PrefixedStream:
    """Re-attaches bytes already read from the front of a stream.

    Sniffing the content type consumes the first few bytes. Wrapping the
    remainder this way keeps the response byte-identical to the stored object
    instead of starting 32 bytes in.
    """

    def __init__(self, head: bytes, rest: BinaryIO) -> None:
        self._head = head
        self._rest = rest

    def read(self, size: int = -1) -> bytes:
        if not self._head:
            return self._rest.read(size)
        if size is None or size < 0:
            head, self._head = self._head, b""
            return head + self._rest.read()
        head, self._head = self._head[:size], self._head[size:]
        if len(head) < size:
            head += self._rest.read(size - len(head))
        return head

    def close(self) -> None:
        self._head = b""
        self._rest.close()

    def __iter__(self):
        # Starlette streams a response body by iterating the file object, so
        # this is what makes a remote object servable at all.
        while True:
            block = self.read(64 * 1024)
            if not block:
                return
            yield block


# --------------------------------------------------------------------------- #
# Storage interface
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StoredMedia:
    asset_id: str
    kind: str
    mime: str
    size: int
    original_name: str
    public_url: str


class MediaStorage(ABC):
    @abstractmethod
    def save(self, stream: BinaryIO, kind: MediaKind, original_name: str, size: int) -> StoredMedia:
        ...

    @abstractmethod
    def resolve(self, asset_id: str) -> Path | None:
        """Map an asset id to a stored path, or None if absent/unreachable."""

    @abstractmethod
    def kind_for(self, path: Path) -> MediaKind:
        ...

    @abstractmethod
    def delete(self, asset_id: str) -> bool:
        ...


class LocalDiskStorage(MediaStorage):
    """Stores files on the local filesystem and serves them from /api/media.

    The `Settings` object is held rather than re-read from the environment. The
    previous version captured a module-level snapshot at import time and signed
    URLs with it, so a runtime override of the storage backend or the signing
    key could leave signing and serving disagreeing.
    """

    def __init__(
        self,
        root: Path,
        base_url: str,
        publicly_reachable: bool = False,
        settings: Settings | None = None,
    ) -> None:
        self._root = root
        self._base_url = base_url
        self._publicly_reachable = publicly_reachable
        self._settings = settings
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def has_public_base(self) -> bool:
        """True only when Buffer can actually download from this URL."""
        return self._publicly_reachable

    def _new_id(self, extension: str) -> str:
        return f"{secrets.token_hex(16)}.{extension}"

    def public_url(self, asset_id: str) -> str:
        if self._base_url:
            return f"{self._base_url}/{asset_id}"
        return asset_id

    def signed_public_url(self, asset_id: str) -> str:
        """Public URL with an HMAC tag so Buffer can fetch it unauthenticated.

        The asset id is already 128 random bits, so this is defence in depth:
        knowing an id from elsewhere is not enough to download the file.
        """
        return f"{self.public_url(asset_id)}{media_signing.sign(asset_id, self._settings)}"

    # -- interface --------------------------------------------------------- #
    def save(self, stream: BinaryIO, kind: MediaKind, original_name: str, size: int) -> StoredMedia:
        asset_id = self._new_id(kind.extension)
        target = self._root / asset_id
        with target.open("wb") as fh:
            shutil.copyfileobj(stream, fh, length=1024 * 256)

        # Measured, not trusted: the size recorded against the asset is what the
        # UI shows and what the publish path checks, and a chunked upload's
        # declared total can disagree with what actually landed on disk.
        stored_size = target.stat().st_size

        logger.info(
            "media.stored",
            extra={"context": {"asset_id": asset_id, "kind": kind.kind, "size": stored_size, "mime": kind.mime}},
        )
        return StoredMedia(
            asset_id=asset_id,
            kind=kind.kind,
            mime=kind.mime,
            size=stored_size,
            original_name=original_name,
            public_url=self.public_url(asset_id),
        )

    def open(self, asset_id: str) -> tuple[BinaryIO, MediaKind, int] | None:
        """Kept for callers that want a stream. Prefer resolve() for responses."""
        path = self.resolve(asset_id)
        if path is None:
            return None
        return path.open("rb"), self.kind_for(path), path.stat().st_size

    def resolve(self, asset_id: str) -> Path | None:
        if not asset_id or "/" in asset_id or "\\" in asset_id or ".." in asset_id:
            return None
        candidate = (self._root / asset_id).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def kind_for(self, path: Path) -> MediaKind:
        extension = path.suffix.lower().lstrip(".")
        candidates = _MIME_BY_EXTENSION.get(extension)
        if candidates:
            return candidates[0]
        return MediaKind("image", "application/octet-stream", extension or "bin")

    def delete(self, asset_id: str) -> bool:
        path = self.resolve(asset_id)
        if path is None:
            return False
        path.unlink(missing_ok=True)
        logger.info("media.deleted", extra={"context": {"asset_id": asset_id}})
        return True


_MIME_BY_EXTENSION: dict[str, list[MediaKind]] = {}
for _offset, _sig, _kind in _SIGNATURES:
    _MIME_BY_EXTENSION.setdefault(_kind.extension, [])
    if _kind not in _MIME_BY_EXTENSION[_kind.extension]:
        _MIME_BY_EXTENSION[_kind.extension].append(_kind)


class S3ObjectStorage(MediaStorage):
    """Stores files in any S3-compatible bucket (Cloudflare R2, AWS S3, B2, MinIO).

    This is the only reliable way to hand media to Buffer. Buffer downloads
    every attachment from the public internet at its own pace, often long after
    the post is created, so the URL must be stable and permanently reachable.
    A local tunnel cannot promise either: a free ngrok URL changes on restart,
    is rate limited, and will break every already-scheduled post that points at
    it.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        access_key: str,
        secret_key: str,
        public_base: str,
        region: str = "auto",
    ) -> None:
        self._bucket = bucket
        self._endpoint = endpoint.rstrip("/")
        self._region = region
        self._public_base = public_base.rstrip("/")
        self._client = _s3_client(endpoint, access_key, secret_key, region)

    @property
    def has_public_base(self) -> bool:
        return bool(self._public_base)

    def _new_id(self, extension: str) -> str:
        return f"{secrets.token_hex(16)}.{extension}"

    def public_url(self, asset_id: str) -> str:
        return f"{self._public_base}/{asset_id}"

    def signed_public_url(self, asset_id: str) -> str:
        """Public URL for an object already served by the bucket.

        Object storage is public by necessity - Buffer fetches the bytes with
        no credential - so there is nothing to sign here. The method exists
        because the upload route calls it on whichever backend it built; without
        it, every upload under MEDIA_STORAGE_BACKEND=s3 raised
        AttributeError and returned 500.

        The access control is the 128-bit unguessable object id plus the
        bucket's own policy, not an HMAC tag.
        """
        return self.public_url(asset_id)

    # -- interface --------------------------------------------------------- #
    def save(self, stream: BinaryIO, kind: MediaKind, original_name: str, size: int) -> StoredMedia:
        asset_id = self._new_id(kind.extension)
        stored_size = self._put(asset_id, stream, kind)
        logger.info(
            "media.stored_s3",
            extra={"context": {"asset_id": asset_id, "kind": kind.kind, "size": stored_size, "bucket": self._bucket}},
        )
        return StoredMedia(
            asset_id=asset_id,
            kind=kind.kind,
            mime=kind.mime,
            size=stored_size,
            original_name=original_name,
            public_url=self.public_url(asset_id),
        )

    def _put(self, key: str, stream: BinaryIO, kind: MediaKind) -> int:
        """Write one object, streaming it in parts. Returns the byte count.

        This used to be a single `stream.read()` fed to `put_object`, which
        pulls the entire file into RAM. That is survivable for a 2 MB logo and
        fatal for the 4 GB Facebook Reel this backend now accepts: the worker
        is killed by the OOM killer long before the upload finishes.

        boto3's managed `upload_fileobj` would also stream, but it reads its
        part size from a TransferConfig and hides the progress, so the multipart
        sequence is spelled out here instead.
        """
        remaining = _remaining(stream)
        if 0 < remaining <= _S3_SINGLE_PUT_LIMIT:
            body = stream.read()
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=kind.mime,
                # Objects are public because Buffer fetches them unauthenticated.
                CacheControl="public, max-age=31536000, immutable",
            )
            return len(body)

        created = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            ContentType=kind.mime,
            CacheControl="public, max-age=31536000, immutable",
        )
        upload_id = created["UploadId"]

        parts: list[dict] = []
        part_number = 0
        written = 0
        try:
            while True:
                block = stream.read(_S3_PART_SIZE)
                if not block:
                    break
                part_number += 1
                written += len(block)
                uploaded = self._client.upload_part(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=block,
                )
                parts.append({"ETag": uploaded["ETag"], "PartNumber": part_number})
        except Exception:
            # An abandoned multipart upload keeps costing storage at the
            # provider's expense until it is explicitly aborted.
            self._abort_multipart(key, upload_id)
            raise

        self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        return written

    def _abort_multipart(self, key: str, upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(
                Bucket=self._bucket, Key=key, UploadId=upload_id
            )
        except Exception:  # pragma: no cover - best effort cleanup
            logger.warning(
                "media.multipart_abort_failed",
                extra={"context": {"key": key, "bucket": self._bucket}},
            )

    def open(self, asset_id: str) -> tuple[BinaryIO, MediaKind, int] | None:
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=asset_id)
        except Exception:
            return None
        body = obj["Body"]
        # The head is pulled only to sniff the type, then handed back to the
        # stream so the response still starts at byte zero.
        head = body.read(_HEAD_SNIFF_BYTES)
        kind = sniff(head)
        if kind is None:
            body.close()
            return None
        size = int(obj.get("ContentLength") or 0)
        return _PrefixedStream(head, body), kind, size

    def resolve(self, asset_id: str) -> Path | None:
        # There is no local path for remote objects. Callers that only need to
        # stream should use open() instead.
        return None

    def kind_for(self, path: Path) -> MediaKind:
        with path.open("rb") as fh:
            kind = sniff(fh.read(32))
        if kind is None:
            raise UnsupportedMedia("Unknown file type.")
        return kind

    def delete(self, asset_id: str) -> bool:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=asset_id)
            return True
        except Exception:
            return False


def _s3_client(endpoint: str, access_key: str, secret_key: str, region: str):
    """Build a boto3 S3 client, addressing virtual-hosted-style buckets.

    Cloudflare R2 requires the bucket in the hostname rather than the path, so
    the address style has to be 'virtual' for that endpoint.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            # R2 and most S3-compatible services are path-style.
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def verify_publicly_reachable(urls: list[str], timeout: float = 12.0) -> tuple[bool, str | None]:
    """Check that Buffer will actually be able to download these URLs.

    Buffer fetches attachments itself, asynchronously, long after a post is
    created. When it cannot reach a URL the post is still created - it simply
    arrives with no image, and the only sign is a missing picture later. That
    is a silent failure, so the URL is fetched here first and the publish is
    refused with a clear reason instead.

    Returns (ok, reason). A successful HEAD proves reachability; a failure to
    reach it proves the post would go out broken.
    """
    import httpx

    for url in urls:
        if not url.startswith(("http://", "https://")):
            return False, f"'{url}' is not a web URL. Buffer cannot download it."
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                # Range avoids pulling a whole video just to prove it exists.
                response = client.get(url, headers={"Range": "bytes=0-1"})
        except httpx.HTTPError as exc:
            return False, (
                f"Buffer cannot reach {url} ({type(exc).__name__}). Media must be hosted "
                "at a permanently public address - a local or tunnel URL is not "
                "reliable for publishing."
            )

        if response.status_code >= 400:
            return False, (
                f"{url} returned HTTP {response.status_code}. Buffer would publish "
                "without the image."
            )
        content_type = response.headers.get("content-type", "")
        if content_type and not (
            content_type.startswith("image/")
            or content_type.startswith("video/")
            or content_type == "application/octet-stream"
        ):
            # A tunnel that serves an HTML warning page returns 200 with text/html,
            # which Buffer will treat as an invalid asset.
            return False, (
                f"{url} returned '{content_type}' instead of an image or video. "
                "This usually means a tunnel is serving its warning page instead "
                "of the file."
            )
    return True, None


def build_storage(settings: Settings) -> MediaStorage:
    """Pick the storage backend from configuration.

    Object storage is preferred whenever it is configured: a local tunnel
    cannot reliably serve Buffer. Falls back to local disk so a developer can
    still run without cloud credentials.
    """
    if settings.media_storage_backend == "s3" and settings.s3_bucket:
        return S3ObjectStorage(
            bucket=settings.s3_bucket,
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key_id,
            secret_key=settings.s3_secret_access_key,
            public_base=settings.s3_public_base_url,
            region=settings.s3_region,
        )

    return LocalDiskStorage(
        root=settings.media_path,
        base_url=settings.media_base_url,
        publicly_reachable=bool(settings.public_media_base_url.strip()),
        settings=settings,
    )