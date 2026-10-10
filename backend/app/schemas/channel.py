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
    platform: str
    display_name: str
    username: str | None = None
    avatar: str | None = None
    description: str | None = None
    active: bool = True
    # --- integration binding (additive, optional) ------------------------
    integration_id: str | None = None
    integration_name: str | None = None
    connection_status: str = "unknown"
    is_locked: bool = False
    queue_paused: bool = False
    capabilities: list[str] = Field(default_factory=list)
    # False when the channel has no working integration behind it. The UI uses
    # this to warn before a publish that would otherwise fail late.
    publishable: bool = True
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