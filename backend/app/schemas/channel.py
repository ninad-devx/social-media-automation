"""Channel schemas.

Note what is absent: the external Buffer channel id and the access token. They
are bound on the server (see `app.services.integration_service`) and are never
serialised to the browser - the UI only ever sees a status.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChannelDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    # The owning brand's name. Declared but never populated until now, so any
    # picker spanning more than one brand had no brand to show and the operator
    # could not tell which brand a channel belonged to before assigning it.
    # Display only: `account_id` stays the authoritative link.
    account_name: str | None = None
    platform: str
    display_name: str
    username: str | None = None
    avatar: str | None = None
    description: str | None = None
    active: bool = True
    # --- archival ---------------------------------------------------------
    # Set when the channel was removed from this application. The row is kept so
    # publishing history and campaign posts keep resolving.
    archived_at: datetime | None = None
    archived_reason: str | None = None
    # --- integration binding (additive, optional) ------------------------
    integration_id: str | None = None
    integration_name: str | None = None
    connection_status: str = "unknown"
    is_locked: bool = False
    queue_paused: bool = False
    capabilities: list[str] = Field(default_factory=list)
    # The backend's authoritative verdict. False when the channel cannot
    # publish; `publish_blocked_reason` then says exactly why, so no selector
    # has to invent an explanation or quietly show it as healthy.
    publishable: bool = True
    publish_blocked_reason: str | None = None
    publish_blocked_code: str | None = None
    last_synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChannelSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    platform: str
    display_name: str
    username: str | None = None
    avatar: str | None = None
    active: bool = True