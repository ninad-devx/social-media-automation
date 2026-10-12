"""Channel endpoints.

Reads (used by every selector in the UI) plus the management operations the
Settings screen needs: enable/disable, manual binding, and refresh.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ApiKeyDep, DbDep, SettingsDep
from app.schemas.channel import ChannelDetail
from app.schemas.integration import ChannelBinding
from app.services import account_service
from app.services.errors import AppError, ErrorCode

router = APIRouter(tags=["channels"])


@router.get(
    "/accounts/{account_id}/channels",
    response_model=list[ChannelDetail],
    dependencies=[ApiKeyDep],
)
def list_account_channels(
    account_id: str,
    db: DbDep,
    include_inactive: bool = Query(False, description="Include inactive channels (admin use)"),
) -> list[ChannelDetail]:
    """Channels for one account.

    React calls this whenever the account selection changes, so adding a
    channel never requires a frontend change.
    """
    account_service.get_account(db, account_id)
    channels = (
        account_service.list_all_channels(db, account_id)
        if include_inactive
        else account_service.list_active_channels(db, account_id)
    )
    return [ChannelDetail(**account_service.channel_summary(c)) for c in channels]


@router.get("/channels", response_model=list[ChannelDetail], dependencies=[ApiKeyDep])
def list_all_channels(db: DbDep) -> list[ChannelDetail]:
    """Every channel across every account. Used by the Posts/Calendar views."""
    from sqlalchemy import select

    from app.models.entities import Account, Channel

    rows = db.scalars(select(Channel).join(Account).order_by(Account.sort_order, Channel.sort_order)).all()
    return [ChannelDetail(**account_service.channel_summary(c)) for c in rows]


# --------------------------------------------------------------------------- #
# management
# --------------------------------------------------------------------------- #
class ChannelUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    active: bool | None = None


def _detail(db, channel_id: str) -> ChannelDetail:
    from app.models.entities import Channel

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")
    return ChannelDetail(**account_service.channel_summary(channel))


@router.patch("/channels/{channel_id}", response_model=ChannelDetail, dependencies=[ApiKeyDep])
def update_channel(channel_id: str, payload: ChannelUpdate, db: DbDep) -> ChannelDetail:
    """Rename or (de)activate a channel. Disabled channels vanish from every selector."""
    from app.models.entities import Channel

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")

    if payload.display_name is not None:
        channel.display_name = payload.display_name.strip()[:120] or channel.display_name
    if payload.description is not None:
        channel.description = payload.description
    if payload.active is not None:
        channel.active = payload.active

    db.commit()
    db.refresh(channel)
    return ChannelDetail(**account_service.channel_summary(channel))


@router.delete(
    "/channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[ApiKeyDep],
)
def delete_channel(channel_id: str, db: DbDep) -> Response:
    """Delete a channel that is no longer wanted.

    Disconnecting an integration only marks its channels `disconnected`, so the
    rows survive by design. This is the way to actually remove one, whether it
    is a leftover from a deleted integration, a duplicate, or a seed row.
    """
    from app.models.entities import Channel

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")

    account_service.delete_channel(db, channel)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/channels/bind", response_model=ChannelDetail, dependencies=[ApiKeyDep]
)
def bind_channel_manually(payload: ChannelBinding, db: DbDep, settings: SettingsDep) -> ChannelDetail:
    """Attach a local channel to an external profile by hand.

    The fallback path when automatic discovery is not possible or not wanted.
    `external_channel_id` is the id Buffer itself shows for that profile; it is
    never guessed from a display name.
    """
    from app.models.entities import Channel
    from app.services.integration_service import new_channel_id

    account_service.get_account(db, payload.account_id)

    local_id = new_channel_id(payload.account_id, payload.external_channel_id)
    channel = db.get(Channel, local_id)
    if channel is None:
        channel = Channel(
            id=local_id,
            account_id=payload.account_id,
            platform=payload.platform.lower(),
            display_name=payload.display_name or payload.external_channel_id,
        )
        db.add(channel)
    elif channel.account_id != payload.account_id:
        raise AppError(ErrorCode.CHANNEL_ALREADY_ASSIGNED, 409, field="external_channel_id")

    channel.external_channel_id = payload.external_channel_id
    channel.external_service = payload.platform.lower()
    if payload.display_name:
        channel.display_name = payload.display_name
    if payload.username:
        channel.external_username = payload.username
        channel.username = payload.username
    channel.active = True

    db.commit()
    db.refresh(channel)
    return ChannelDetail(**account_service.channel_summary(channel))


@router.post("/channels/{channel_id}/refresh", response_model=ChannelDetail, dependencies=[ApiKeyDep])
def refresh_channel(channel_id: str, db: DbDep, settings: SettingsDep) -> ChannelDetail:
    """Re-read one channel from its provider and update local state.

    Used by the "Synchronization required" fix-it button in Settings.
    """
    from app.models.entities import Channel
    from app.services.integration_service import IntegrationService

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")
    if not channel.integration_id:
        raise AppError(ErrorCode.CHANNEL_NOT_BOUND, 409, field="channel_id")

    service = IntegrationService(db, settings)
    integration = service.get(channel.integration_id)
    if not integration.external_organization_id:
        raise AppError(ErrorCode.INTEGRATION_NOT_TESTED, 409, field="integration_id")

    from app.services.buffer_client import BufferApiError

    try:
        remote = service._client_for(integration).get_channel(  # noqa: SLF001
            integration.external_organization_id, channel.external_channel_id
        )
    except BufferApiError as exc:
        raise AppError(exc.code, 502, field="channel_id") from exc

    if remote is None:
        channel.connection_status = "reauth_required"
        db.commit()
        db.refresh(channel)
        return ChannelDetail(**account_service.channel_summary(channel))

    service._apply_remote(  # noqa: SLF001
        channel, remote, integration, activate=channel.active
    )
    integration.last_sync_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(channel)
    return ChannelDetail(**account_service.channel_summary(channel))