"""Account and channel read models.

Everything here is data-driven: adding an account is a row, not a code change.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import Account, Channel
from app.services.errors import AppError, ErrorCode

if TYPE_CHECKING:  # pragma: no cover
    from app.schemas.account import AccountCreate, AccountUpdate


def _channel_count(db: Session, account_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Channel)
            .where(Channel.account_id == account_id, Channel.active.is_(True))
        )
        or 0
    )


def list_active_accounts(db: Session) -> list[Account]:
    """Active accounts that have at least one active channel.

    Filters out accounts that cannot publish anything, so the UI never offers a
    dead end.
    """
    stmt = (
        select(Account)
        .where(Account.active.is_(True))
        .where(
            Account.id.in_(
                select(Channel.account_id).where(Channel.active.is_(True))
            )
        )
        .order_by(Account.sort_order, Account.name)
    )
    return list(db.scalars(stmt).all())


def list_all_accounts(db: Session) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.sort_order, Account.name)).all())


def get_account(db: Session, account_id: str) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, status.HTTP_404_NOT_FOUND, field="account_id")
    return account


def get_active_account(db: Session, account_id: str) -> Account:
    account = get_account(db, account_id)
    if not account.active:
        raise AppError(ErrorCode.ACCOUNT_INACTIVE, status.HTTP_409_CONFLICT, field="account_id")
    return account


def list_active_channels(db: Session, account_id: str) -> list[Channel]:
    stmt = (
        select(Channel)
        .where(Channel.account_id == account_id, Channel.active.is_(True))
        .order_by(Channel.sort_order, Channel.display_name)
    )
    return list(db.scalars(stmt).all())


def list_all_channels(db: Session, account_id: str) -> list[Channel]:
    stmt = select(Channel).where(Channel.account_id == account_id).order_by(Channel.sort_order)
    return list(db.scalars(stmt).all())


def resolve_channel(db: Session, account_id: str, channel_id: str) -> Channel:
    """Resolve a channel and enforce that it belongs to the given account.

    This is a defensive re-implementation of the same rule enforced inside the
    n8n workflow. It exists so the user gets an instant, accurate error instead
    of a round trip, and so the application never asks the publishing engine to
    do work it cannot succeed at.
    """
    account = get_active_account(db, account_id)

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, status.HTTP_404_NOT_FOUND, field="channel_id")

    if channel.account_id != account.id:
        raise AppError(
            ErrorCode.CHANNEL_NOT_BELONG_TO_ACCOUNT,
            status.HTTP_404_NOT_FOUND,
            field="channel_id",
        )

    if not channel.active:
        raise AppError(ErrorCode.CHANNEL_INACTIVE, status.HTTP_409_CONFLICT, field="channel_id")

    return channel


def platform_label(platform: str) -> str:
    return {
        "instagram": "Instagram",
        "facebook": "Facebook",
        "linkedin": "LinkedIn",
        "twitter": "X",
        "x": "X",
        "threads": "Threads",
    }.get(platform.lower(), platform.title())


def account_summary(account: Account, db: Session) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "type": account.type,
        "logo": account.logo,
        "color": account.color,
        "active": account.active,
        "channel_count": _channel_count(db, account.id),
    }


def _capabilities(channel: Channel) -> list[str]:
    import json

    raw = channel.capabilities_json
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(c) for c in parsed] if isinstance(parsed, list) else []


def is_publishable(channel: Channel) -> bool:
    """Whether this channel has a working integration behind it.

    A channel can be perfectly valid and still be unable to publish - a
    disabled integration, a Buffer channel that went disconnected. The UI uses
    this to warn early instead of letting the user discover it after clicking
    Publish.

    Channels with NO external binding are reported as publishable on purpose.
    Those predate this feature and publish through the n8n workflow, which owns
    its own channel registry. The backend genuinely cannot tell whether that
    workflow knows a given channel, so claiming "will not publish" would be a
    false alarm on an existing install. The publish call is still the final
    authority and fails with a precise error if the workflow does not know it.
    """
    if not channel.external_channel_id:
        # Never linked to anything: legacy channel, unknown but working.
        return True

    if not channel.integration_id:
        # Had an external id but lost its integration (deleted or disabled).
        # The mapping is orphaned, so it genuinely cannot publish.
        return False

    if channel.is_locked or channel.connection_status == "reauth_required":
        return False

    integration = channel.integration
    if integration is None or not integration.enabled:
        return False
    return integration.status not in ("connection_failed", "disabled", "reauth_required")


def channel_summary(channel: Channel) -> dict:
    return {
        "id": channel.id,
        "account_id": channel.account_id,
        "platform": channel.platform,
        "display_name": channel.display_name,
        "username": channel.username,
        "avatar": channel.avatar,
        "description": channel.description,
        "active": channel.active,
        "integration_id": channel.integration_id,
        "integration_name": channel.integration.display_name if channel.integration else None,
        # "unknown" for channels that predate integrations, so the UI does not
        # render them as broken.
        "connection_status": channel.connection_status,
        "is_locked": channel.is_locked,
        "queue_paused": channel.queue_paused,
        "capabilities": _capabilities(channel),
        "publishable": is_publishable(channel),
        "last_synced_at": channel.last_synced_at,
    }


def ensure_known_account(db: Session, account_id: str) -> None:
    if db.get(Account, account_id) is None:
        raise HTTPException(status_code=500, detail="account table not seeded")


# --------------------------------------------------------------------------- #
# brand management
# --------------------------------------------------------------------------- #
def _next_sort_order(db: Session) -> int:
    current = db.scalar(select(func.coalesce(func.max(Account.sort_order), -1)))
    return int(current or 0) + 1


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug[:48] or "account"


def unique_account_id(db: Session, requested: str | None, name: str) -> str:
    """A stable id that is guaranteed not to collide.

    Falls back to the slug, then appends a suffix, so creating "Glaamora" twice
    produces two usable brands rather than a 500 from the primary key.
    """
    base = (requested or "").strip() or _slugify(name)
    candidate = base
    suffix = 2
    while db.get(Account, candidate) is not None:
        candidate = f"{base}_{suffix}"
        suffix += 1
        if suffix > 100:  # pragma: no cover - pathological
            raise AppError(ErrorCode.ACCOUNT_ID_EXHAUSTED, 409, field="id")
    return candidate


def create_account(db: Session, payload: "AccountCreate") -> Account:
    """Add a brand. No code or seed edit required."""
    account = Account(
        id=unique_account_id(db, payload.id, payload.name),
        name=payload.name.strip()[:160],
        type=(payload.type or "brand").strip()[:64],
        description=payload.description,
        logo=payload.logo,
        color=payload.color,
        active=True,
        sort_order=payload.sort_order if payload.sort_order is not None else _next_sort_order(db),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def update_account(db: Session, account: Account, payload: "AccountUpdate") -> Account:
    """Edit or (de)activate a brand."""
    for field_name in ("name", "type", "description", "logo", "color", "active", "sort_order"):
        value = getattr(payload, field_name)
        if value is None:
            continue
        if field_name in ("name", "type"):
            value = value.strip()[:160 if field_name == "name" else 64]
        setattr(account, field_name, value)

    db.commit()
    db.refresh(account)
    return account


def delete_channel(db: Session, channel: Channel) -> None:
    """Remove a single channel.

    Channels arrive from three places that nobody can undo from the UI:
    Buffer discovery, the manual bind form, and the legacy seed rows. A channel
    that is no longer wanted has to be removable, otherwise it sits in every
    brand's selector forever.

    Only `publish_logs` carries a channel id and it is deliberately left
    alone: it is history of what was attempted, not a live reference, and
    there is no foreign key to satisfy.
    """
    db.delete(channel)
    db.commit()


def delete_account(db: Session, account: Account) -> None:
    """Remove a brand.

    Guarded: a brand that still has publishable channels is refused, because
    deleting it would silently re-point scheduled posts at nothing.
    """
    if account.channels:
        raise AppError(
            ErrorCode.ACCOUNT_STILL_HAS_CHANNELS,
            409,
            field="account_id",
            details=[{"channel_count": len(account.channels)}],
        )
    db.delete(account)
    db.commit()