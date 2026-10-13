"""Channel endpoints.

Reads (used by every selector in the UI) plus the management operations the
Settings screen needs: enable/disable, archive/restore, manual binding, and
refresh.

Read-path contract, enforced by :func:`app.services.account_service.channel_query`
so it cannot drift:

* ordinary views show active, unarchived channels belonging to active brands;
* the administrative view is opt-in via ``include_inactive`` /
  ``include_inactive_accounts``;
* publishing history is never filtered by channel state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
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
    include_inactive: bool = Query(False, description="Include inactive or archived channels (admin use)"),
) -> list[ChannelDetail]:
    """Channels for one account.

    React calls this whenever the account selection changes, so adding a
    channel never requires a frontend change.
    """
    account_service.get_account(db, account_id)
    channels = account_service.list_all_channels(
        db, account_id, include_inactive=include_inactive
    )
    return [ChannelDetail(**account_service.channel_summary(c)) for c in channels]


@router.get("/channels", response_model=list[ChannelDetail], dependencies=[ApiKeyDep])
def list_all_channels(
    db: DbDep,
    include_inactive: bool = Query(False, description="Include inactive or archived channels (admin use)"),
    include_inactive_accounts: bool = Query(
        False, description="Include channels belonging to disabled brands (admin use)"
    ),
) -> list[ChannelDetail]:
    """Every channel across every account, for administrative views.

    Defaults to the same set the publishing selectors see. The Settings screen
    asks for the administrative view explicitly; nothing else should need it.
    """
    stmt = account_service.channel_query(
        include_inactive=include_inactive,
        include_inactive_accounts=include_inactive_accounts,
    )
    rows = db.scalars(stmt).all()
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
    """Rename or (de)activate a channel. Disabled channels vanish from every selector.

    An archived channel is not reactivated here: restoring an archived channel
    does not restore its Buffer binding, so it goes through
    ``POST /channels/{id}/restore`` and then a fresh import.
    """
    from app.models.entities import Channel

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")

    if payload.display_name is not None:
        channel.display_name = payload.display_name.strip()[:120] or channel.display_name
    if payload.description is not None:
        channel.description = payload.description
    if payload.active is not None:
        if payload.active and channel.archived_at is not None:
            raise AppError(
                ErrorCode.CHANNEL_ARCHIVED, 409, field="active"
            )
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
def archive_channel(
    channel_id: str,
    db: DbDep,
    settings: SettingsDep,
    reason: Annotated[str | None, Query(max_length=64)] = None,
) -> Response:
    """Remove a channel from this application.

    Archival, not deletion, and deliberately so:

    * the row survives, so publishing history and campaign posts that reference
      ``channel_id`` keep resolving and the audit trail stays intact;
    * the Buffer side is untouched - no disconnect, no delete, no post is
      affected. Buffer channels are not owned by this application;
    * the external assignment is released, so the same Buffer profile can later
      be imported into a different brand. The unique index on
      ``external_channel_id`` guarantees only one local row can ever claim it.

    Use ``POST /channels/{id}/restore`` to undo.
    """
    from app.models.entities import Channel
    from app.services.integration_service import IntegrationService

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")

    if channel.archived_at is not None:
        # Idempotent: archiving an already-archived channel is not an error.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    IntegrationService(db, settings).archive_channel(channel, reason=reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/channels/{channel_id}/restore", response_model=ChannelDetail, dependencies=[ApiKeyDep])
def restore_channel(channel_id: str, db: DbDep, settings: SettingsDep) -> ChannelDetail:
    """Clear the archive mark.

    This does NOT re-link the channel to Buffer: the external assignment was
    released on purpose, so re-acquiring it has to go through the normal import
    flow where ownership is verified. The restored row comes back as
    "not linked" and is reported as not publishable, which is the truth.
    """
    from app.models.entities import Channel
    from app.services.integration_service import IntegrationService

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")
    if channel.archived_at is None:
        return ChannelDetail(**account_service.channel_summary(channel))

    IntegrationService(db, settings).restore_channel(channel)
    return ChannelDetail(**account_service.channel_summary(channel))


class ChannelReassign(BaseModel):
    """Move a channel to another brand, explicitly."""

    account_id: str = Field(min_length=1, max_length=64)


@router.post("/channels/{channel_id}/reassign", response_model=ChannelDetail, dependencies=[ApiKeyDep])
def reassign_channel(channel_id: str, payload: ChannelReassign, db: DbDep) -> ChannelDetail:
    """Move a channel to a different brand.

    Explicit by design: the audit found that a Buffer profile was pinned to one
    brand forever with no way out, because the local id is derived from the
    external id alone and a cross-account import was refused outright. Reassignment
    is therefore a first-class, audited operation rather than something that
    happens as a side effect of a re-import.

    Publishing history is NOT rewritten - it keeps pointing at the channel id, so
    a post that went out under the old brand still reads back under the old
    brand in history. Only future publishing follows the new owner.
    """
    from app.models.entities import Channel

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")

    target = account_service.get_account(db, payload.account_id)
    if not target.active:
        raise AppError(ErrorCode.ACCOUNT_INACTIVE, 409, field="account_id")
    if channel.account_id == target.id:
        return ChannelDetail(**account_service.channel_summary(channel))
    if channel.archived_at is not None:
        raise AppError(ErrorCode.CHANNEL_ARCHIVED, 409, field="channel_id")

    channel.account_id = target.id
    db.commit()
    db.refresh(channel)
    return ChannelDetail(**account_service.channel_summary(channel))


@router.post(
    "/channels/bind", response_model=ChannelDetail, dependencies=[ApiKeyDep]
)
def bind_channel_manually(payload: ChannelBinding, db: DbDep, settings: SettingsDep) -> ChannelDetail:
    """Attach a local channel to a Buffer profile by hand.

    The fallback path when automatic discovery is not possible or not wanted.
    ``external_channel_id`` is the id Buffer shows for that profile; it is never
    guessed from a display name.

    ``integration_id`` is mandatory. The previous version accepted a bare
    external id and wrote ``external_channel_id`` without ``integration_id``,
    which produced a channel that appeared linked and could never publish,
    because the publishing path resolves the integration to decrypt a token.
    There is no safe way to infer which integration was meant, so the caller
    names it and the profile is verified against that workspace first.
    """
    from app.services.integration_service import IntegrationService

    row = IntegrationService(db, settings).bind_channel(
        account_id=payload.account_id,
        integration_id=payload.integration_id,
        external_channel_id=payload.external_channel_id,
        platform=payload.platform,
        display_name=payload.display_name,
        username=payload.username,
    )
    return ChannelDetail(**account_service.channel_summary(row))


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
    if channel.archived_at is not None:
        raise AppError(ErrorCode.CHANNEL_ARCHIVED, 409, field="channel_id")
    if not channel.integration_id:
        raise AppError(ErrorCode.CHANNEL_NOT_BOUND, 409, field="channel_id")

    service = IntegrationService(db, settings)
    integration = service.get(channel.integration_id)
    if not integration.external_organization_id:
        raise AppError(ErrorCode.INTEGRATION_NOT_TESTED, 409, field="integration_id")

    from app.services.buffer_client import BufferApiError

    try:
        remote = service.client_for(integration).get_channel(  # noqa: SLF001
            integration.external_organization_id, channel.external_channel_id
        )
    except BufferApiError as exc:
        raise AppError(exc.code, 502, field="channel_id") from exc

    if remote is None:
        channel.connection_status = "reauth_required"
        db.commit()
        db.refresh(channel)
        return ChannelDetail(**account_service.channel_summary(channel))

    service.apply_remote(  # noqa: SLF001
        channel, remote, integration, activate=channel.active
    )
    integration.last_sync_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(channel)
    return ChannelDetail(**account_service.channel_summary(channel))