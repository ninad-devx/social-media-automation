"""Media upload / download / delete endpoints.

Uploads stream to disk in chunks so a large file never sits in memory, and the
content type is decided by magic bytes rather than by whatever the browser
claimed in its Content-Type header.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from app.api.deps import ApiKeyDep, DbDep, SettingsDep
from app.core.logging import get_logger
from app.models.entities import MediaAsset
from app.schemas.media import MediaAssetOut, MediaUploadResponse
from app.services import media_signing
from app.services.errors import AppError, ErrorCode
from app.services.storage import MediaTooLarge, build_storage, sniff

logger = get_logger("app.media")
router = APIRouter(tags=["media"])

# Enough bytes to cover every signature we inspect.
_HEAD_BYTES = 32
_CHUNK = 1024 * 256


class _LimitedStream:
    """Wraps an upload stream and raises once the size limit is passed."""

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


@router.post("/media/upload", response_model=MediaUploadResponse, dependencies=[ApiKeyDep])
async def upload_media(
    db: DbDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Image or video file")],
) -> MediaUploadResponse:
    """Accept a desktop upload and return a URL for the publishing engine."""
    try:
        head = await file.read(_HEAD_BYTES)
        await file.seek(0)

        if not head:
            raise AppError(ErrorCode.MEDIA_INVALID, field="file")

        kind = sniff(head)
        if kind is None:
            raise AppError(ErrorCode.MEDIA_INVALID, field="file")

        declared = file.size or 0
        if declared and declared > settings.max_upload_bytes:
            raise AppError(ErrorCode.MEDIA_INVALID, field="file")

        storage = build_storage(settings)
        try:
            stored = storage.save(
                _LimitedStream(file.file, settings.max_upload_bytes),
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

    publicly_reachable = storage.has_public_base
    preview_base = settings.preview_media_base_url.rstrip("/")
    # Buffer fetches this unauthenticated, so it carries an HMAC tag.
    signed_public_url = storage.signed_public_url(stored.asset_id)

    db.add(
        MediaAsset(
            asset_id=stored.asset_id,
            filename=stored.original_name[:255],
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
        preview_url=f"{preview_base}/{stored.asset_id}",
        kind=stored.kind,
        mime=stored.mime,
        size=stored.size,
        filename=stored.original_name,
        publicly_reachable=publicly_reachable,
        warning=None
        if publicly_reachable
        else (
            "Saved locally. Buffer runs in the cloud and cannot download a localhost URL, "
            "so publishing this media needs PUBLIC_MEDIA_BASE_URL set to a public address "
            "(for example a cloudflared tunnel)."
        ),
    )


@router.get("/media", response_model=list[MediaAssetOut], dependencies=[ApiKeyDep])
def list_media(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[MediaAssetOut]:
    """Recently uploaded media. Foundation for a future media library."""
    rows = db.scalars(select(MediaAsset).order_by(MediaAsset.created_at.desc()).limit(limit)).all()
    return [MediaAssetOut.model_validate(a, from_attributes=True) for a in rows]


@router.get("/media/{asset_id}", dependencies=[ApiKeyDep])
def get_media(asset_id: str, settings: SettingsDep) -> FileResponse:
    """Serve an uploaded file."""
    storage = build_storage(settings)
    path = storage.resolve(asset_id)
    if path is None:
        raise AppError(ErrorCode.NOT_FOUND, 404)

    # FileResponse takes a path (its first positional arg), not a file object.
    return FileResponse(
        path=path,
        media_type=storage.kind_for(path).mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get("/media/public/{asset_id}")
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

    storage = build_storage(settings)
    path = storage.resolve(asset_id)
    if path is None:
        raise AppError(ErrorCode.NOT_FOUND, 404)

    return FileResponse(
        path=path,
        media_type=storage.kind_for(path).mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.delete("/media/{asset_id}", status_code=204, dependencies=[ApiKeyDep])
def delete_media(asset_id: str, settings: SettingsDep, db: DbDep) -> Response:
    """Remove an uploaded file."""
    build_storage(settings).delete(asset_id)

    asset = db.get(MediaAsset, asset_id)
    if asset is not None:
        db.delete(asset)
        db.commit()

    return Response(status_code=204)