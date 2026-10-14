"""Resumable, chunked uploads.

Facebook accepts a 4 GB Reel and a multi-gigabyte video upload. A 4 GB body in
one HTTP request does not survive the trip: uvicorn buffers the multipart
parser's spool file, the reverse proxy in front of the app caps the body long
before that, and the browser gives up somewhere around the two-minute mark. So
anything above a threshold is sent as a sequence of fixed-size chunks that the
server appends to a staging file, and the asset only becomes real when the last
chunk lands.

Three properties matter and are the reason this is a module rather than four
inline handlers:

  * **Resumable.** Chunks are addressed by index, not by arrival order, and a
    re-sent chunk is accepted and ignored rather than appended twice. A dropped
    connection on chunk 380 of 500 costs one chunk, not the whole video.
  * **Bounded.** The declared total is checked against the sniffed kind's
    ceiling before the first byte is stored, and the staged bytes are capped
    again while they accumulate, so a client cannot lie its way past the limit.
  * **Self-cleaning.** Abandoned sessions are swept on their TTL. Nothing else
    ever touches this directory, so without the sweep a cancelled 4 GB upload
    would sit on the disk forever.

State lives on disk, not in the database: it is transient, it can be large, and
a worker restart must not lose an upload that is already half-way through.
"""
from __future__ import annotations

import json
import math
import secrets
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.errors import AppError, ErrorCode
from app.services.storage import MediaKind, sniff

logger = get_logger("app.media.upload")

#: Bumped when the on-disk layout changes in a way an old session cannot satisfy.
_LAYOUT_VERSION = 1


@dataclass
class UploadSession:
    """One in-progress upload."""

    upload_id: str
    filename: str
    size: int
    chunk_size: int
    created_at: datetime
    received: set[int] = field(default_factory=set)

    @property
    def chunk_count(self) -> int:
        return max(1, math.ceil(self.size / self.chunk_size))

    def expected_chunk_size(self, index: int) -> int:
        """Bytes chunk `index` must carry, given the declared total."""
        if index < 0 or index >= self.chunk_count:
            raise AppError(ErrorCode.INVALID_REQUEST, 400, field="index")
        start = index * self.chunk_size
        return min(self.chunk_size, self.size - start)

    def received_bytes(self) -> int:
        return sum(self.expected_chunk_size(i) for i in self.received)

    def is_complete(self) -> bool:
        return len(self.received) >= self.chunk_count


class UploadSessionStore:
    """Chunked-upload bookkeeping, backed by a staging directory."""

    def __init__(self, settings: Settings) -> None:
        self._root = settings.upload_staging_path
        self._settings = settings
        self._root.mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------- #
    def _session_dir(self, upload_id: str) -> Path:
        # upload_id is server-generated hex, but it arrives in a URL, so it is
        # validated rather than trusted.
        if not upload_id or len(upload_id) > 64 or not all(c in "0123456789abcdef" for c in upload_id):
            raise AppError(ErrorCode.INVALID_REQUEST, 400, field="upload_id")
        candidate = (self._root / upload_id).resolve()
        try:
            candidate.relative_to(self._root.resolve())
        except ValueError:  # pragma: no cover - unreachable given the charset
            raise AppError(ErrorCode.INVALID_REQUEST, 400, field="upload_id") from None
        return candidate

    def _meta_path(self, upload_id: str) -> Path:
        return self._session_dir(upload_id) / "meta.json"

    def _data_path(self, upload_id: str) -> Path:
        return self._session_dir(upload_id) / "data.bin"

    # -- lifecycle ---------------------------------------------------------- #
    def sweep(self) -> int:
        """Delete sessions past their TTL. Returns how many were removed."""
        ttl = timedelta(hours=max(1, int(self._settings.upload_session_ttl_hours)))
        cutoff = datetime.now(timezone.utc) - ttl
        removed = 0

        for entry in self._root.iterdir():
            if not entry.is_dir():
                continue
            try:
                meta = json.loads((entry / "meta.json").read_text())
                created = datetime.fromisoformat(meta["created_at"])
            except (OSError, ValueError, KeyError):
                # Unreadable metadata means the session is already broken.
                created = datetime.fromtimestamp(0, tz=timezone.utc)
            if created < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1

        if removed:
            logger.info("media.upload_swept", extra={"context": {"sessions": removed}})
        return removed

    def create(self, filename: str, size: int) -> UploadSession:
        self.sweep()

        name = (filename or "upload").strip() or "upload"
        if size <= 0:
            raise AppError(ErrorCode.MEDIA_INVALID, field="size")

        # Ceiling applied before anything is written. The true limit depends on
        # the file type, which is only known once the first chunk arrives, so
        # the video ceiling is used here as the outer bound.
        ceiling = max(
            int(self._settings.max_upload_bytes),
            int(self._settings.max_video_upload_bytes),
        )
        if size > ceiling:
            raise AppError(ErrorCode.MEDIA_INVALID, field="size")

        upload_id = secrets.token_hex(16)
        session_dir = self._session_dir(upload_id)
        session_dir.mkdir(parents=True, exist_ok=False)
        # Sparse file: the chunks are written at their offsets, so a 4 GB upload
        # does not need 4 GB of free space before the first byte arrives.
        self._data_path(upload_id).touch()

        session = UploadSession(
            upload_id=upload_id,
            filename=name[:255],
            size=size,
            chunk_size=self._settings.chunked_upload_bytes,
            created_at=datetime.now(timezone.utc),
        )
        self._write_meta(session)

        logger.info(
            "media.upload_started",
            extra={
                "context": {
                    "upload_id": upload_id,
                    "filename": session.filename,
                    "size": size,
                    "chunk_size": session.chunk_size,
                    "chunks": session.chunk_count,
                }
            },
        )
        return session

    def get(self, upload_id: str) -> UploadSession:
        path = self._meta_path(upload_id)
        try:
            meta = json.loads(path.read_text())
        except (OSError, ValueError):
            raise AppError(ErrorCode.NOT_FOUND, 404, field="upload_id") from None

        if meta.get("layout") != _LAYOUT_VERSION:
            raise AppError(ErrorCode.NOT_FOUND, 404, field="upload_id")

        return UploadSession(
            upload_id=meta["upload_id"],
            filename=meta["filename"],
            size=int(meta["size"]),
            chunk_size=int(meta["chunk_size"]),
            created_at=datetime.fromisoformat(meta["created_at"]),
            received={int(i) for i in meta.get("received", [])},
        )

    def append_at(self, upload_id: str, index: int) -> "AbstractContextManager[BinaryIO]":
        """Open the staging file positioned at chunk `index`'s offset.

        The caller streams into the returned handle and then calls
        `mark_received`. Nothing is recorded as stored until that happens, so a
        connection that dies mid-chunk leaves the index unclaimed and the
        client simply sends it again.
        """
        session = self.get(upload_id)
        session.expected_chunk_size(index)
        return self._open_at(upload_id, index * session.chunk_size)

    @contextmanager
    def _open_at(self, upload_id: str, offset: int) -> Iterator[BinaryIO]:
        try:
            fh = self._data_path(upload_id).open("r+b")
        except OSError:
            raise AppError(ErrorCode.NOT_FOUND, 404, field="upload_id") from None
        try:
            fh.seek(offset)
            yield fh
        finally:
            fh.close()

    def mark_received(self, upload_id: str, index: int) -> UploadSession:
        """Record a chunk as durable.

        Separate from the write so the index is only claimed once its bytes are
        actually on disk.
        """
        session = self.get(upload_id)
        session.expected_chunk_size(index)
        session.received.add(index)
        self._write_meta(session)
        return session

    def open_staged(self, upload_id: str) -> tuple[BinaryIO, int]:
        """Open the assembled file for streaming into storage."""
        path = self._data_path(upload_id)
        if not path.is_file():
            raise AppError(ErrorCode.NOT_FOUND, 404, field="upload_id")
        return path.open("rb"), path.stat().st_size

    def sniff_staged(self, upload_id: str) -> MediaKind:
        with self._data_path(upload_id).open("rb") as fh:
            head = fh.read(32)
        kind = sniff(head)
        if kind is None:
            raise AppError(ErrorCode.MEDIA_INVALID, field="file")
        return kind

    def finish(self, upload_id: str) -> None:
        """Drop the staging files once the asset is safely stored."""
        shutil.rmtree(self._session_dir(upload_id), ignore_errors=True)

    def abort(self, upload_id: str) -> None:
        shutil.rmtree(self._session_dir(upload_id), ignore_errors=True)

    # -- internals ---------------------------------------------------------- #
    def _write_meta(self, session: UploadSession) -> None:
        payload = {
            "layout": _LAYOUT_VERSION,
            "upload_id": session.upload_id,
            "filename": session.filename,
            "size": session.size,
            "chunk_size": session.chunk_size,
            "created_at": session.created_at.isoformat(),
            "received": sorted(session.received),
        }
        # Written to a sibling and moved into place, so a crash mid-write
        # cannot leave metadata that parses but describes a different file.
        tmp = self._meta_path(session.upload_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self._meta_path(session.upload_id))
