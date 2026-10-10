"""Integration request/response schemas.

The response models are the security boundary: `credential_encrypted` has no
representation here, so no code path can leak a stored Buffer token by
accidentally serialising an ORM row.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class IntegrationCreate(BaseModel):
    provider: str = Field(default="buffer", max_length=32)
    display_name: str = Field(default="Buffer", max_length=160)
    # Write-only. Stored encrypted; never echoed back.
    api_key: str = Field(min_length=1, max_length=512)
    # Optional: pick a specific Buffer workspace when the token can see several.
    organization_id: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    test_connection: bool = True


class IntegrationUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=160)
    enabled: bool | None = None
    # Present only when rotating the credential.
    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    organization_id: str | None = Field(default=None, max_length=64)


class OrganizationOut(BaseModel):
    id: str
    name: str


class IntegrationOut(BaseModel):
    id: str
    provider: str
    display_name: str
    enabled: bool
    status: str
    # Masked tail only, e.g. "..." - never the token.
    credential_hint: str | None = None
    credential_stored: bool = False
    external_organization_id: str | None = None
    external_organization_name: str | None = None
    available_organizations: list[OrganizationOut] = Field(default_factory=list)
    channel_count: int = 0
    active_channel_count: int = 0
    last_tested_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConnectionTestOut(BaseModel):
    ok: bool
    status: str
    integration_id: str
    organization_id: str | None = None
    organization_name: str | None = None
    message: str
    checked_at: datetime


class DiscoveredChannel(BaseModel):
    """A channel as Buffer describes it, plus how we already map it."""

    external_channel_id: str
    service: str
    name: str
    display_name: str | None = None
    descriptor: str | None = None
    channel_type: str | None = None
    avatar: str | None = None
    external_link: str | None = None
    timezone: str | None = None
    disconnected: bool = False
    locked: bool = False
    queue_paused: bool = False
    # False when this engine cannot publish to that platform.
    supported: bool = True
    local_channel_id: str | None = None
    local_account_id: str | None = None
    already_imported: bool = False


class DiscoverOut(BaseModel):
    integration_id: str
    organization_id: str
    organization_name: str | None = None
    channels: list[DiscoveredChannel]
    total: int
    importable: int


class ChannelSyncRequest(BaseModel):
    """Import the chosen Buffer channels as local channels for one account."""

    account_id: str = Field(min_length=1, max_length=64)
    external_channel_ids: list[str] = Field(min_length=1)
    activate: bool = True


class ChannelSyncOut(BaseModel):
    integration_id: str
    account_id: str
    created: int
    updated: int
    skipped: int
    channel_ids: list[str] = Field(default_factory=list)
    synced_at: datetime


class ChannelBinding(BaseModel):
    """Manual binding, used when automatic discovery is unavailable."""

    account_id: str = Field(min_length=1, max_length=64)
    external_channel_id: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=32)
    display_name: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=160)


class SystemStatusOut(BaseModel):
    """Central integration overview for the Settings dashboard."""

    environment: str
    publish_engine: str
    backend: dict
    database: dict
    n8n: dict
    encryption: dict
    media: dict
    integrations: dict
    ai: dict
    counts: dict
    recent_failures: list[dict]
    issues: list[dict]
