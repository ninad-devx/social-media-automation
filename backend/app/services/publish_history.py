"""Publishing history: exactly one row per logical attempt.

The audit found every publish producing two rows. With ``PUBLISH_ENGINE=n8n`` the
browser-facing route wrote a provisional row, and then the n8n workflow called
``/internal/publish``, which wrote a second one for the same post. They even
carried different ``request_id`` values, because ``build_n8n_payload`` never
forwarded the id the backend had already generated - so the workflow minted its
own. Nothing deduplicated them and nothing said they were the same attempt.

The fix is ownership plus a single write path:

* the browser-facing route owns the provisional row;
* ``/internal/publish`` is the authoritative confirmation of what Buffer
  actually did, so it *updates* the row the caller already created rather than
  adding one;
* both go through :func:`record_publish_attempt` / :func:`confirm_publish_attempt`
  here, keyed on ``request_id``, so a duplicate is impossible by construction
  even if a future caller forgets the rule;
* ``request_id`` is now propagated to n8n, so the correlation is real rather
  than coincidental.

Existing rows are never rewritten or deleted. Duplicates already in the table
are left exactly as they are - they are history, and reconciling them is a
separate, explicitly-approved operation (see ``reports/duplicate_history.md``).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import PublishLog


def find_by_request_id(db: Session, request_id: str) -> PublishLog | None:
    if not request_id:
        return None
    return db.scalars(
        select(PublishLog).where(PublishLog.request_id == request_id)
    ).first()


def record_publish_attempt(
    db: Session,
    *,
    request_id: str,
    account_id: str,
    channel_id: str,
    platform: str,
    content: str,
    media_count: int,
    publish_mode: str,
    scheduled_at: datetime | None,
    timezone_name: str,
    idempotency_key: str | None = None,
    source: str = "dashboard",
    status: str = "pending",
) -> PublishLog:
    """Create the provisional history row, or return the existing one.

    Idempotent on ``request_id``: if a row already exists for this attempt - the
    n8n callback arriving after the caller already recorded it, for instance -
    the existing row is returned instead of a duplicate being created.
    """
    existing = find_by_request_id(db, request_id)
    if existing is not None:
        return existing

    row = PublishLog(
        request_id=request_id,
        idempotency_key=idempotency_key,
        account_id=account_id,
        channel_id=channel_id,
        platform=platform,
        content=content,
        content_preview=content[:280],
        media_count=media_count,
        publish_mode=publish_mode,
        scheduled_at=scheduled_at,
        timezone=timezone_name,
        status=status,
        source=source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def confirm_publish_attempt(
    db: Session,
    *,
    request_id: str,
    status: str,
    account_id: str | None = None,
    channel_id: str | None = None,
    platform: str | None = None,
    buffer_publication_id: str | None = None,
    external_link: str | None = None,
    scheduled_at: datetime | None = None,
    duration_ms: int | None = None,
    source: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    n8n_request_id: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> PublishLog:
    """Record the confirmed outcome of an attempt, creating or updating.

    The caller that already created the provisional row gets it updated. A
    confirmed outcome that arrives without a provisional row (the internal route
    being called directly) creates exactly one row, still keyed on the same
    ``request_id``.
    """
    row = find_by_request_id(db, request_id)

    if row is None:
        from app.models.entities import Account, Channel

        if not account_id or not channel_id:
            # Without the provisional row there is nothing to correlate against,
            # and guessing ids would fabricate history. Refuse loudly instead.
            raise ValueError(
                "confirm_publish_attempt needs account_id and channel_id when no "
                f"provisional row exists for request_id={request_id!r}"
            )
        account = db.get(Account, account_id)
        channel = db.get(Channel, channel_id)
        row = PublishLog(
            request_id=request_id,
            idempotency_key=None,
            account_id=account_id,
            channel_id=channel_id,
            platform=platform_of(channel, platform),
            content="",
            content_preview="",
            media_count=0,
            publish_mode="NOW",
            scheduled_at=scheduled_at,
            timezone="UTC",
            status=status,
            source=source or "n8n_internal",
        )
        db.add(row)

    row.status = status
    if buffer_publication_id is not None:
        row.buffer_publication_id = buffer_publication_id
    if external_link is not None:
        row.external_link = external_link
    if scheduled_at is not None:
        row.scheduled_at = scheduled_at
    if duration_ms is not None:
        row.duration_ms = duration_ms
    if source is not None:
        row.source = source
    if n8n_request_id is not None:
        row.n8n_request_id = n8n_request_id
    # Diagnostics are preserved rather than cleared: a row that ends up failed
    # after a successful provider call should still show what happened.
    if error_code is not None:
        row.error_code = error_code
    if error_message is not None:
        row.error_message = error_message
    for key, value in (extra_fields or {}).items():
        if hasattr(row, key):
            setattr(row, key, value)

    db.commit()
    db.refresh(row)
    return row


def platform_of(channel: Any, fallback: str) -> str:
    if channel is not None:
        value = getattr(channel, "platform", None)
        if value:
            return str(value)
    return fallback or "unknown"


# --------------------------------------------------------------------------- #
# Removing history (local only)
# --------------------------------------------------------------------------- #
def delete_history_entry(db: Session, log_id: int) -> bool:
    """Remove one history row. Returns False when it was already gone.

    Local bookkeeping only. Nothing is sent to Buffer: a row here records what
    the application *attempted*, and deleting it does not delete, edit or cancel
    anything on a social channel. A post that really went out stays live on
    Buffer regardless - only this app's record of it disappears.

    Called only from an explicit operator action. Nothing in the publishing path
    deletes history, so an attempt can never erase its own record.
    """
    row = db.get(PublishLog, log_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def clear_history(
    db: Session,
    *,
    account_id: str | None = None,
    channel_id: str | None = None,
    status_filter: str | None = None,
    platform: str | None = None,
    before: datetime | None = None,
) -> int:
    """Delete history matching the filters. Returns how many rows went.

    Requires at least one filter. An unscoped "delete everything" is too easy to
    fire by accident from a stray click, and history is the only record this
    application keeps of what it tried to publish.
    """
    stmt = select(PublishLog)

    if account_id:
        stmt = stmt.where(PublishLog.account_id == account_id)
    if channel_id:
        stmt = stmt.where(PublishLog.channel_id == channel_id)
    if status_filter:
        stmt = stmt.where(PublishLog.status == status_filter)
    if platform:
        stmt = stmt.where(PublishLog.platform == platform.lower())
    if before is not None:
        stmt = stmt.where(PublishLog.created_at < before)

    rows = list(db.scalars(stmt).all())
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)


# --------------------------------------------------------------------------- #
# Duplicate reporting (read-only)
# --------------------------------------------------------------------------- #
def duplicate_history_report(db: Session) -> list[dict[str, Any]]:
    """Find existing duplicate history rows. Read-only; changes nothing.

    Reported rather than cleaned up: these rows are real records of real
    attempts, and deciding which one to keep is an operator judgement. Run it
    deliberately, review the output, and act only with approval.
    """
    rows = db.scalars(select(PublishLog).order_by(PublishLog.created_at)).all()

    # Group on the stable facts of an attempt. `request_id` is included because
    # the pre-fix duplicates have different ones - which is precisely why they
    # were undetectable.
    groups: dict[tuple, list[PublishLog]] = {}
    for row in rows:
        key = (
            row.account_id,
            row.channel_id,
            row.platform,
            row.content_preview,
            row.publish_mode,
            str(row.scheduled_at),
        )
        groups.setdefault(key, []).append(row)

    duplicates: list[dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (r.created_at, r.id))
        duplicates.append(
            {
                "account_id": key[0],
                "channel_id": key[1],
                "platform": key[2],
                "publish_mode": key[4],
                "scheduled_at": key[5],
                "copies": len(group),
                "row_ids": [r.id for r in group],
                "request_ids": [r.request_id for r in group],
                "created_at": [r.created_at.isoformat() if r.created_at else None for r in group],
                "statuses": [r.status for r in group],
            }
        )
    duplicates.sort(key=lambda d: (-d["copies"], d["channel_id"]))
    return duplicates