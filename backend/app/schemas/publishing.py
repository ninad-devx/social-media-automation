"""Publishing schemas.

PublishRequest mirrors the n8n workflow contract exactly so the adapter layer
is a pass-through rather than a translation of something else.

Fields kept for future phases (source, campaign_id, approval_status,
created_by, ai_generated, content_id) are intentionally declared as optional
and currently unused, so adding them later is not a breaking change.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MediaType = Literal["image", "video"]


class PublishMode(str, Enum):
    NOW = "NOW"
    SCHEDULE = "SCHEDULE"


class MediaItem(BaseModel):
    model_config = ConfigDict(extra="allow")  # tolerate future fields like asset_id

    type: MediaType
    url: str
    alt_text: str | None = None
    asset_id: str | None = None

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        v = (v or "").strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("media url must be an http(s) URL")
        return v


class PublishRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    account_id: Annotated[str, Field(min_length=1, max_length=64)]
    channel_id: Annotated[str, Field(min_length=1, max_length=64)]
    content: Annotated[str, Field(max_length=65000)]
    media: list[MediaItem] = Field(default_factory=list)
    publish_mode: PublishMode = PublishMode.NOW
    scheduled_at: datetime | None = None
    timezone: str = Field(default="Asia/Dhaka", max_length=64)

    # Explicit post type: post, carousel, reel, story, video, long_form.
    # Optional - when omitted the platform's default is used. An unsupported
    # value is refused before anything is sent, never silently downgraded to a
    # feed post.
    post_type: str | None = Field(default=None, max_length=32)

    # --- reserved for later phases, accepted but not yet acted upon --------
    source: str | None = Field(default="dashboard", max_length=32)
    campaign_id: str | None = Field(default=None, max_length=64)
    approval_status: str | None = Field(default=None, max_length=32)
    created_by: str | None = Field(default=None, max_length=128)
    ai_generated: bool = False
    content_id: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        import zoneinfo

        v = (v or "").strip()
        try:
            zoneinfo.ZoneInfo(v)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone, e.g. Asia/Dhaka") from exc
        return v


class PublishResponse(BaseModel):
    success: bool
    request_id: str
    status: Literal["published", "scheduled"]
    account_id: str
    channel_id: str
    platform: str
    account_name: str | None = None
    channel_name: str | None = None

    buffer_publication_id: str | None = None
    external_link: str | None = None
    scheduled_at: datetime | None = None
    timezone: str | None = None
    content_length: int = 0
    media_count: int = 0
    idempotent_replay: bool = False

    # n8n's own correlation id, for log cross-referencing only.
    n8n_request_id: str | None = None


class PublishLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: str
    account_id: str
    # Resolved from the accounts table for display. The audit found Posts and
    # Calendar rendering a raw UUID where a brand name belongs.
    account_name: str | None = None
    channel_id: str
    channel_name: str | None = None
    platform: str
    content_preview: str
    media_count: int
    publish_mode: str
    post_type: str | None = None
    scheduled_at: datetime | None = None
    timezone: str
    status: str
    buffer_publication_id: str | None = None
    external_link: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None


class DuplicateHistoryGroup(BaseModel):
    """One set of history rows that describe the same attempt."""

    account_id: str
    channel_id: str
    platform: str
    publish_mode: str
    scheduled_at: str | None
    copies: int
    row_ids: list[int]
    request_ids: list[str]
    created_at: list[str | None]
    statuses: list[str]


class DuplicateHistoryReport(BaseModel):
    """Read-only reconciliation input. Never applied automatically."""

    generated_at: datetime
    total_rows: int
    duplicate_groups: list[DuplicateHistoryGroup]
    redundant_rows: int
    note: str = (
        "Reported for review only. No rows were changed. Reconciling history is "
        "an operator decision because each row records a real attempt."
    )


class PublishLogPage(BaseModel):
    items: list[PublishLogOut]
    total: int
    limit: int
    offset: int