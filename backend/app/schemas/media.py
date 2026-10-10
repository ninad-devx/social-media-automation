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