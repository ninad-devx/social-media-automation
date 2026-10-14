"""Media upload / download / delete endpoints.

Two ways in, both landing on the same storage and the same asset row:

  * `POST /api/media/upload` - one multipart request. Right for images.
  * `POST /api/media/uploads` + chunk puts + `complete` - right for the 4 GB
    Facebook Reel this app now accepts, because a body that size does not
    survive a single request through a reverse proxy.

Both validate by magic bytes rather than by whatever the browser claimed in its
Content-Type header, and both stream to disk in chunks so a large file never
sits in memory.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select

from app.api.deps import ApiKeyDep, DbDep, SettingsDep
from app.core.logging import get_logger
from app.models.entities import MediaAsset
from app.schemas.media import (
    MediaAssetOut,
    MediaUploadResponse,
    UploadChunkOut,
    UploadLimitsOut,
    UploadStartRequest,
    UploadStatusOut,
)
from app.services import media_signing
from app.services.errors import AppError, ErrorCode
from app.services.storage import MediaTooLarge, build_storage, sniff
from app.services.upload_sessions import UploadSession, UploadSessionStore

logger = get_logger("app.media")
router = APIRouter(tags=["media"])

# Enough bytes to cover every signature we inspect.
_HEAD_BYTES = 32
_CHUNK = 1024 * 256

#: Applied to every served asset, local or remote.
_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "inline",
    "Cache-Control": "public, max-age=31536000, immutable",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


class _LimitedStream:
    """Wraps an upload stream and raises once the size limit is passed.

    `tell` and `seek` are delegated rather than dropped: the storage layer uses
    them to learn how many bytes are left before it chooses between a single
    object write and a multipart one, and without them every upload looked
    unsizably large.
    """

    def __init__(self, fh, limit: int) -> None:
        self._fh = fh
        self._limit = limit
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size)
        self._read += len(chunk)
        if self._read > self._limit:
            raise MediaTooLarge()
        return chunk

    def tell(self) -> int:
        return self._fh.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._fh.seek(offset, whence)


def _record_upload(db, storage, stored, *, original_name: str) -> MediaUploadResponse:
    """Persist a stored file and build the response both upload paths return."""
    publicly_reachable = storage.has_public_base
    # Buffer fetches this unauthenticated, so it carries an HMAC tag.
    signed_public_url = storage.signed_public_url(stored.asset_id)

    db.add(
        MediaAsset(
            asset_id=stored.asset_id,
            filename=original_name[:255],
            kind=stored.kind,
            mime=stored.mime,
            size=stored.size,
            url=signed_public_url,
            public=publicly_reachable,
        )
    )
    db.commit()

    logger.info(
        "media.upload",
        extra={
            "context": {
                "asset_id": stored.asset_id,
                "kind": stored.kind,
                "size": stored.size,
                "public": publicly_reachable,
            }
        },
    )

    return MediaUploadResponse(
        asset_id=stored.asset_id,
        url=signed_public_url,
        # Same signed URL as `url`, and deliberately so.
        #
        # A preview is rendered by an <img> tag, which cannot send the
        # X-API-Key header. Pointing preview_url at the authenticated
        # /api/media/{id} route therefore always returned 401 and showed a
        # broken image. The signed public URL is fetched by plain GET, which is
        # exactly what a browser and Buffer both do.
        preview_url=signed_public_url,
        kind=stored.kind,
        mime=stored.mime,
        size=stored.size,
        filename=original_name,
        publicly_reachable=publicly_reachable,
        warning=None
        if publicly_reachable
        else (
            "Saved locally. Buffer runs in the cloud and cannot download a localhost URL, "
            "so publishing this media needs PUBLIC_MEDIA_BASE_URL set to a public address "
            "(for example a cloudflared tunnel)."
        ),
    )


@router.get("/media/upload-limits", response_model=UploadLimitsOut, dependencies=[ApiKeyDep])
def media_upload_limits(settings: SettingsDep) -> UploadLimitsOut:
    """The ceilings this deployment enforces.

    The browser needs these before it starts a multi-gigabyte transfer: failing
    at the file picker costs nothing, failing after an hour of upload costs the
    user their time and the server the bandwidth.
    """
    return UploadLimitsOut(
        image_max_bytes=settings.upload_limit_for("image"),
        video_max_bytes=settings.upload_limit_for("video"),
        chunk_threshold_bytes=settings.upload_chunk_threshold_bytes,
        chunk_bytes=settings.chunked_upload_bytes,
    )


@router.post("/media/upload", response_model=MediaUploadResponse, dependencies=[ApiKeyDep])
async def upload_media(
    db: DbDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Image or video file")],
) -> MediaUploadResponse:
    """Accept a desktop upload and return a URL for the publishing engine.

    The ceiling depends on what the bytes turn out to be: a 30 MB PNG is still
    refused, while a 2 GB MP4 goes through, because that is a size Facebook and
    Instagram both accept for a video or a Reel.
    """
    try:
        head = await file.read(_HEAD_BYTES)
        await file.seek(0)

        if not head:
            raise AppError(ErrorCode.MEDIA_INVALID, field="file")

        kind = sniff(head)
        if kind is None:
            raise AppError(ErrorCode.MEDIA_INVALID, field="file")

        limit = settings.upload_limit_for(kind.kind)

        declared = file.size or 0
        if declared and declared > limit:
            raise AppError(ErrorCode.MEDIA_INVALID, field="file")

        storage = build_storage(settings)
        try:
            stored = storage.save(
                _LimitedStream(file.file, limit),
                kind,
                file.filename or "upload",
                declared,
            )
        except MediaTooLarge:
            raise AppError(ErrorCode.MEDIA_INVALID, field="file") from None
        except OSError:
            logger.error("media.store_failed", extra={"context": {"filename": file.filename}})
            raise AppError(ErrorCode.INTERNAL_ERROR, 500) from None
    finally:
        await file.close()

    return _record_upload(db, storage, stored, original_name=stored.original_name)


# --------------------------------------------------------------------------- #
# chunked / resumable upload
# --------------------------------------------------------------------------- #
def _status(session: UploadSession) -> UploadStatusOut:
    return UploadStatusOut(
        upload_id=session.upload_id,
        filename=session.filename,
        size=session.size,
        chunk_size=session.chunk_size,
        chunk_count=session.chunk_count,
        received=sorted(session.received),
        received_bytes=session.received_bytes(),
        complete=session.is_complete(),
    )


@router.post("/media/uploads", response_model=UploadStatusOut, status_code=201, dependencies=[ApiKeyDep])
def start_chunked_upload(
    payload: UploadStartRequest,
    settings: SettingsDep,
) -> UploadStatusOut:
    """Open an upload session and learn how the file must be split.

    The server picks the chunk size rather than accepting the client's: S3
    rejects multipart parts under 5 MiB, so a browser that chose its own size
    would only find out at `complete`, after the whole file had been sent.
    """
    store = UploadSessionStore(settings)
    return _status(store.create(payload.filename, payload.size))


@router.get("/media/uploads/{upload_id}", response_model=UploadStatusOut, dependencies=[ApiKeyDep])
def chunked_upload_status(upload_id: str, settings: SettingsDep) -> UploadStatusOut:
    """Which chunks the server already holds, so a dropped upload can resume."""
    return _status(UploadSessionStore(settings).get(upload_id))


@router.put(
    "/media/uploads/{upload_id}/chunks/{index}",
    response_model=UploadChunkOut,
    dependencies=[ApiKeyDep],
)
async def put_chunk(
    upload_id: str,
    index: Annotated[int, Path(ge=0)],
    request: Request,
    settings: SettingsDep,
) -> UploadChunkOut:
    """Store one chunk of a large upload.

    The body is the raw chunk, not multipart, so nothing has to be re-parsed or
    buffered: it is written straight to the staging file at its offset.
    """
    store = UploadSessionStore(settings)
    session = store.get(upload_id)
    expected = session.expected_chunk_size(index)

    # An already-stored chunk is acknowledged without being read again, which is
    # what makes a client-side retry safe.
    if index in session.received:
        return UploadChunkOut(**_status(session).model_dump())

    written = 0
    try:
        with store.append_at(upload_id, index) as fh:
            async for block in request.stream():
                written += len(block)
                if written > expected:
                    raise AppError(ErrorCode.MEDIA_INVALID, field="chunk")
                fh.write(block)
    except AppError:
        # A chunk that overruns its slot means the stream is not what the client
        # promised, so the offsets past this point can no longer be trusted.
        store.abort(upload_id)
        raise
    except OSError:
        logger.error("media.chunk_write_failed", extra={"context": {"upload_id": upload_id}})
        store.abort(upload_id)
        raise AppError(ErrorCode.INTERNAL_ERROR, 500) from None

    if written != expected:
        store.abort(upload_id)
        raise AppError(ErrorCode.MEDIA_INVALID, field="chunk")

    session = store.mark_received(upload_id, index)
    logger.info(
        "media.chunk_stored",
        extra={"context": {"upload_id": upload_id, "index": index, "received": len(session.received)}},
    )
    return UploadChunkOut(**_status(session).model_dump())


@router.post(
    "/media/uploads/{upload_id}/complete",
    response_model=MediaUploadResponse,
    dependencies=[ApiKeyDep],
)
def complete_chunked_upload(
    upload_id: str,
    db: DbDep,
    settings: SettingsDep,
) -> MediaUploadResponse:
    """Validate the assembled file and store it as a real asset."""
    store = UploadSessionStore(settings)
    session = store.get(upload_id)

    if not session.is_complete():
        missing = sorted(set(range(session.chunk_count)) - session.received)
        raise AppError(ErrorCode.INVALID_REQUEST, 400, field="upload_id")

    kind = store.sniff_staged(upload_id)

    # The kind-aware ceiling is re-applied to the bytes that actually arrived:
    # the declared size was checked before the first chunk and cannot be
    # trusted on its own.
    if session.size > settings.upload_limit_for(kind.kind):
        store.abort(upload_id)
        raise AppError(ErrorCode.MEDIA_INVALID, field="file")

    storage = build_storage(settings)
    handle, actual_size = store.open_staged(upload_id)
    try:
        stored = storage.save(handle, kind, session.filename, actual_size)
    except MediaTooLarge:
        store.abort(upload_id)
        raise AppError(ErrorCode.MEDIA_INVALID, field="file") from None
    except OSError:
        store.abort(upload_id)
        logger.error("media.store_failed", extra={"context": {"upload_id": upload_id}})
        raise AppError(ErrorCode.INTERNAL_ERROR, 500) from None
    finally:
        handle.close()

    # Only now is the staging copy redundant; until storage has it, an abort
    # would destroy the upload.
    store.finish(upload_id)

    return _record_upload(db, storage, stored, original_name=session.filename)


@router.delete("/media/uploads/{upload_id}", status_code=204, dependencies=[ApiKeyDep])
def abort_chunked_upload(upload_id: str, settings: SettingsDep) -> Response:
    """Throw away an upload the user cancelled."""
    UploadSessionStore(settings).abort(upload_id)
    return Response(status_code=204)


def _serve(storage, asset_id: str) -> FileResponse | StreamingResponse:
    """Serve an asset from either storage backend.

    `LocalDiskStorage.resolve()` returns a path; `S3ObjectStorage.resolve()`
    returns None by design because a remote object has no local file. The two
    GET routes used to treat None as "not found" and 404 on every request under
    `MEDIA_STORAGE_BACKEND=s3`, which is the documented production setting.
    """
    path = storage.resolve(asset_id)
    if path is not None:
        return FileResponse(
            path=path,
            media_type=storage.kind_for(path).mime,
            headers=_HEADERS,
        )

    handle = storage.open(asset_id)
    if handle is None:
        raise AppError(ErrorCode.NOT_FOUND, 404)

    stream, kind, _size = handle
    return StreamingResponse(
        stream,
        media_type=kind.mime,
        headers=_HEADERS,
    )


@router.get("/media", response_model=list[MediaAssetOut], dependencies=[ApiKeyDep])
def list_media(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[MediaAssetOut]:
    """Recently uploaded media. Foundation for a future media library."""
    rows = db.scalars(select(MediaAsset).order_by(MediaAsset.created_at.desc()).limit(limit)).all()
    return [MediaAssetOut.model_validate(a, from_attributes=True) for a in rows]


@router.api_route(
    "/media/{asset_id}",
    methods=["GET", "HEAD"],
    response_class=FileResponse,
    dependencies=[ApiKeyDep],
)
def get_media(asset_id: str, settings: SettingsDep) -> FileResponse:
    """Serve an uploaded file."""
    return _serve(build_storage(settings), asset_id)


@router.api_route(
    "/media/public/{asset_id}",
    methods=["GET", "HEAD"],
    response_class=FileResponse,
)
def get_media_public(
    asset_id: str,
    request: Request,
    settings: SettingsDep,
    s: Annotated[str | None, Query(description="HMAC tag on the asset id")] = None,
    e: Annotated[int | None, Query(description="Expiry, unix seconds")] = None,
) -> FileResponse:
    """Serve an uploaded file to Buffer, with no credential.

    Deliberately NOT behind `ApiKeyDep`: Buffer is a third party that cannot
    send our API key, and its download failure is silent - the post is simply
    created with no picture.

    Access control is the signed tag instead:
      * the asset id is 128 bits of `secrets.token_hex`, so it cannot be
        enumerated;
      * `s` is an HMAC-SHA256 over `asset_id:expiry` keyed by a server secret
        derived from CREDENTIAL_ENCRYPTION_KEY, verified in constant time;
      * `e` bounds how long the URL stays usable.

    Knowing an asset id is therefore not sufficient to download the bytes, and a
    URL captured from a post cannot be replayed indefinitely.
    """
    if not media_signing.has_signing_key(settings):
        raise AppError(ErrorCode.NOT_FOUND, 404)

    if not media_signing.verify(asset_id, s, e, settings):
        logger.warning("media.public_denied", extra={"context": {"asset_id": asset_id}})
        raise AppError(ErrorCode.NOT_FOUND, 404)

    return _serve(build_storage(settings), asset_id)


@router.delete("/media/{asset_id}", status_code=204, dependencies=[ApiKeyDep])
def delete_media(asset_id: str, settings: SettingsDep, db: DbDep) -> Response:
    """Remove an uploaded file."""
    build_storage(settings).delete(asset_id)

    asset = db.get(MediaAsset, asset_id)
    if asset is not None:
        db.delete(asset)
        db.commit()

    return Response(status_code=204)