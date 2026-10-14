"""Media schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MediaUploadResponse(BaseModel):
    """Result of a desktop upload."""

    asset_id: str
    url: str
    kind: str                 # "image" | "video"
    mime: str
    size: int
    filename: str

    # False when the URL is only reachable from this machine. Buffer cannot
    # download such a URL, so the UI surfaces this before the user publishes.
    publicly_reachable: bool = False
    warning: str | None = None

    # URL the browser uses for thumbnails. Always points at this API directly,
    # so previews work even when `url` points at a tunnel with a browser
    # interstitial. Falls back to `url` when unset.
    preview_url: str | None = None


class MediaAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_id: str
    filename: str
    kind: str
    mime: str
    size: int
    url: str
    public: bool
    created_at: datetime | None = None


class UploadLimitsOut(BaseModel):
    """What this deployment accepts, so the UI never invents a ceiling.

    The browser used to hardcode 25 MB while the server enforced its own
    number, so raising one of them silently left the other refusing the file.
    The limits are read from settings on every call instead.
    """

    image_max_bytes: int
    video_max_bytes: int
    #: Above this size a file is sent as chunks rather than one request.
    chunk_threshold_bytes: int
    chunk_bytes: int


class UploadStartRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


class UploadStatusOut(BaseModel):
    """State of an in-progress upload, so a client can resume."""

    upload_id: str
    filename: str
    size: int
    chunk_size: int
    chunk_count: int
    received: list[int] = Field(default_factory=list)
    received_bytes: int = 0
    complete: bool = False


class UploadChunkOut(UploadStatusOut):
    """Acknowledgement for one stored chunk."""
