"""Account and channel read models.

Everything here is data-driven: adding an account is a row, not a code change.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

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


def channel_query(include_inactive: bool = False, include_inactive_accounts: bool = False):
    """The one channel query every read path uses.

    Three separate lists used to disagree about what "a channel" meant, which is
    why a channel an operator had removed kept reappearing on the brand page.
    This builder is the single definition:

    * archived and inactive channels are excluded by default;
    * channels belonging to an inactive brand are excluded by default, because a
      disabled brand cannot publish;
    * both exclusions are lifted explicitly by the administrative views.

    Publishing history is deliberately NOT filtered by any of this - a post that
    really went out stays visible even after its channel is archived.
    """
    # `account` is eager-loaded because channel_summary reads `account.name` for
    # every row, and `integration` because it reads `integration.display_name`.
    # Without this, listing a brand's channels is one extra query per channel.
    stmt = (
        select(Channel)
        .join(Account, Channel.account_id == Account.id)
        .options(selectinload(Channel.account), selectinload(Channel.integration))
    )
    if not include_inactive:
        stmt = stmt.where(Channel.active.is_(True), Channel.archived_at.is_(None))
    if not include_inactive_accounts:
        stmt = stmt.where(Account.active.is_(True))
    return stmt.order_by(Account.sort_order, Channel.sort_order, Channel.display_name)


def list_active_accounts(db: Session) -> list[Account]:
    """Active accounts that have at least one publishable channel.

    Filters out accounts that cannot publish anything, so the UI never offers a
    dead end.
    """
    active_channel_ids = select(Channel.account_id).where(
        Channel.active.is_(True), Channel.archived_at.is_(None)
    )
    stmt = (
        select(Account)
        .where(Account.active.is_(True))
        .where(Account.id.in_(active_channel_ids))
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
        .where(
            Channel.account_id == account_id,
            Channel.active.is_(True),
            Channel.archived_at.is_(None),
        )
        .order_by(Channel.sort_order, Channel.display_name)
    )
    return list(db.scalars(stmt).all())


def list_all_channels(
    db: Session, account_id: str, *, include_inactive: bool = False
) -> list[Channel]:
    """Channels for one account.

    `include_inactive` is the administrative view and is opt-in, so a removed
    channel does not reappear in the ordinary selectors.
    """
    stmt = (
        select(Channel)
        .where(Channel.account_id == account_id)
        .options(selectinload(Channel.account), selectinload(Channel.integration))
    )
    if not include_inactive:
        stmt = stmt.where(Channel.active.is_(True), Channel.archived_at.is_(None))
    return list(db.scalars(stmt.order_by(Channel.sort_order, Channel.display_name)).all())


def is_archived(channel: Channel) -> bool:
    return channel.archived_at is not None


def resolve_channel(db: Session, account_id: str, channel_id: str) -> Channel:
    """Resolve a channel and enforce that it belongs to the given account.

    This is a defensive re-implementation of the same rule enforced inside the
    n8n workflow. It exists so the user gets an instant, accurate error instead
    of a round trip, and so the application never asks the publishing engine to
    do work it cannot succeed at.

    This is the last gate before a publish is accepted, so it also enforces
    eligibility: an archived channel can never be published to, whichever
    endpoint asked.
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

    if channel.archived_at is not None:
        raise AppError(ErrorCode.CHANNEL_ARCHIVED, status.HTTP_409_CONFLICT, field="channel_id")

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
    """Whether this channel can actually publish right now.

    Deprecated in favour of :func:`eligibility`, which also explains *why*. Kept
    as a thin wrapper because it is the cheapest possible question and it is
    used in list comprehensions.
    """
    return eligibility(channel).publishable


class Eligibility:
    """The authoritative publishability verdict for one channel.

    `publishable` is the decision; `reason_code` and `reason` are what the UI
    shows so a channel is never labelled healthy when it is not. The audit found
    `is_locked`, `connection_status`, `queue_paused` and `publishable` being
    computed here and then ignored by every frontend selector, so the verdict and
    its explanation now travel together.
    """

    __slots__ = ("publishable", "reason_code", "reason")

    def __init__(self, publishable: bool, reason_code: str | None = None, reason: str | None = None):
        self.publishable = publishable
        self.reason_code = reason_code
        self.reason = reason

    def as_dict(self) -> dict:
        return {
            "publishable": self.publishable,
            "reason_code": self.reason_code,
            "reason": self.reason,
        }


def eligibility(channel: Channel) -> Eligibility:
    """Decide whether a channel can publish, and say why when it cannot."""
    if channel.archived_at is not None:
        return Eligibility(
            False,
            ErrorCode.CHANNEL_ARCHIVED,
            "This channel was removed and is no longer linked to Buffer.",
        )
    if not channel.active:
        return Eligibility(
            False, ErrorCode.CHANNEL_INACTIVE, "This channel is currently disabled."
        )

    if not channel.external_channel_id:
        # Never linked to anything. The backend genuinely cannot tell whether a
        # legacy n8n registry knows this id, so it is reported as unavailable
        # rather than as a false alarm. A publish attempt is still the final
        # authority and fails with a precise error.
        return Eligibility(
            False,
            ErrorCode.CHANNEL_NOT_BOUND,
            "This channel is not linked to a Buffer channel yet. Link it in Settings.",
        )

    if not channel.integration_id:
        return Eligibility(
            False,
            ErrorCode.CHANNEL_NOT_BOUND,
            "This channel has no integration. Connect it in Settings.",
        )

    if channel.is_locked:
        return Eligibility(
            False,
            ErrorCode.CHANNEL_LOCKED,
            "Buffer has this channel locked, so it cannot receive posts.",
        )

    if channel.connection_status == "reauth_required":
        return Eligibility(
            False,
            ErrorCode.CHANNEL_REAUTH_REQUIRED,
            "Buffer reports this channel as disconnected. Reconnect it in Buffer.",
        )

    integration = channel.integration
    if integration is None:
        return Eligibility(
            False,
            ErrorCode.INTEGRATION_NOT_FOUND,
            "The integration this channel was published through no longer exists.",
        )
    if not integration.enabled:
        return Eligibility(
            False,
            ErrorCode.INTEGRATION_DISABLED,
            f"Integration '{integration.display_name}' is disabled.",
        )
    if integration.status in ("connection_failed", "disabled", "reauth_required"):
        return Eligibility(
            False,
            ErrorCode.INTEGRATION_REAUTH_REQUIRED,
            f"Integration '{integration.display_name}' is not connected: "
            f"{integration.last_error_message or integration.status.replace('_', ' ')}.",
        )
    if not integration.external_organization_id:
        return Eligibility(
            False,
            ErrorCode.INTEGRATION_NOT_TESTED,
            f"Integration '{integration.display_name}' has no workspace selected.",
        )

    if channel.queue_paused:
        # Buffer accepts the post but will hold it in a paused queue, so it
        # looks like it published and does not. Worth surfacing, but it does not
        # make the channel unusable.
        return Eligibility(
            True,
            "QUEUE_PAUSED",
            "Buffer has this channel's queue paused, so posts will be held "
            "until the queue is resumed.",
        )

    return Eligibility(True)


def channel_summary(channel: Channel) -> dict:
    verdict = eligibility(channel)
    return {
        "id": channel.id,
        "account_id": channel.account_id,
        # The owning brand's display name. This existed on the response schema
        # but was never populated, so any view listing channels from more than
        # one brand showed bare profile names and nothing told the operator
        # which brand a channel belonged to. Any-channel-to-any-brand only
        # works if the picker can show it.
        "account_name": channel.account.name if channel.account else None,
        "platform": channel.platform,
        "display_name": channel.display_name,
        "username": channel.username,
        "avatar": channel.avatar,
        "description": channel.description,
        "active": channel.active,
        "archived_at": channel.archived_at,
        "archived_reason": channel.archived_reason,
        "integration_id": channel.integration_id,
        "integration_name": channel.integration.display_name if channel.integration else None,
        # "unknown" for channels that predate integrations, so the UI does not
        # render them as broken.
        "connection_status": channel.connection_status,
        "is_locked": channel.is_locked,
        "queue_paused": channel.queue_paused,
        "capabilities": _capabilities(channel),
        "publishable": verdict.publishable,
        # Always populated when `publishable` is False, so the UI can explain
        # instead of silently greying the row out.
        "publish_blocked_reason": verdict.reason,
        "publish_blocked_code": verdict.reason_code,
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


def account_protected_references(db: Session, account_id: str) -> dict:
    """Everything that must survive the removal of a brand.

    Deleting a brand used to be refused because `account.channels` was truthy -
    an unfiltered relationship, so a single channel that had once existed blocked
    the brand forever with no way out. This counts the references that actually
    carry history, and lets the caller offer a safe archive instead.
    """
    from app.models.ai_entities import CampaignPost
    from app.models.entities import PublishLog

    return {
        "active_channels": int(
            db.scalar(
                select(func.count())
                .select_from(Channel)
                .where(
                    Channel.account_id == account_id,
                    Channel.active.is_(True),
                    Channel.archived_at.is_(None),
                )
            )
            or 0
        ),
        "archived_channels": int(
            db.scalar(
                select(func.count())
                .select_from(Channel)
                .where(Channel.account_id == account_id, Channel.archived_at.is_not(None))
            )
            or 0
        ),
        "publish_logs": int(
            db.scalar(
                select(func.count()).select_from(PublishLog).where(
                    PublishLog.account_id == account_id
                )
            )
            or 0
        ),
        "campaign_posts": int(
            db.scalar(
                select(func.count()).select_from(CampaignPost).where(
                    CampaignPost.account_id == account_id
                )
            )
            or 0
        ),
    }


def delete_account(db: Session, account: Account) -> None:
    """Remove a brand, but only when nothing references it.

    Hard deletion cascades to `channels`, which would erase the channel rows
    that publishing history and campaign posts point at. So the brand is only
    deleted when it is genuinely empty; anything with history is refused with an
    actionable message pointing at the archive path (deactivate), which removes
    it from every selector while preserving the audit trail.
    """
    refs = account_protected_references(db, account.id)

    if refs["publish_logs"] or refs["campaign_posts"]:
        raise AppError(
            ErrorCode.ACCOUNT_HAS_HISTORY,
            409,
            field="account_id",
            details=[refs],
        )

    if refs["active_channels"] or refs["archived_channels"]:
        raise AppError(
            ErrorCode.ACCOUNT_STILL_HAS_CHANNELS,
            409,
            field="account_id",
            details=[refs],
        )

    db.delete(account)
    db.commit()