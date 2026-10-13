"""POST /api/publish and publishing history endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import ApiKeyDep, DbDep, N8nDep
from app.core.config import get_settings
from app.core.logging import get_logger, request_id_var
from app.models.entities import Account, Channel, PublishLog
from app.schemas.publishing import (
    DuplicateHistoryReport,
    PublishLogOut,
    PublishLogPage,
    PublishRequest,
    PublishResponse,
)
from app.services.publish_service import PublishService

logger = get_logger("app.publishing")

router = APIRouter(tags=["publishing"])


@router.post(
    "/publish",
    response_model=PublishResponse,
    status_code=200,    dependencies=[ApiKeyDep],
)
def publish(
    payload: PublishRequest,
    db: DbDep,
    n8n: N8nDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-Id")] = None,
) -> PublishResponse:
    """Validate, resolve, and hand the post to the n8n publishing engine.

    `Idempotency-Key` is optional but recommended: sending the same key twice
    replays the first result instead of publishing twice.

    A successful publish is also mirrored into the campaign list so the post
    can be found, reviewed and managed alongside AI campaigns.
    """
    from app.api.request_id import new_request_id

    request_id = x_request_id or new_request_id()
    token = request_id_var.set(request_id)
    try:
        service = PublishService(db, get_settings(), n8n)
        response = service.publish(
            payload, request_id=request_id, idempotency_key=idempotency_key
        )

        # Never let the bookkeeping turn a successful publish into an error.
        try:
            from app.services.campaign_service import CampaignService

            CampaignService(db, get_settings()).record_quick_publish(
                content=payload.content,
                targets=[
                    {"account_id": payload.account_id, "channel_id": payload.channel_id}
                ],
                media=[
                    # The request holds MediaItem objects, not dicts.
                    {
                        "type": getattr(m, "type", None) or "image",
                        "url": getattr(m, "url", None),
                        "alt_text": getattr(m, "alt_text", None),
                    }
                    for m in (payload.media or [])
                ],
                publish_response=response,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "publish.campaign_mirror_failed",
                extra={
                    "context": {
                        "error_type": type(exc).__name__,
                        # Server-side only, so a mirroring bug is diagnosable
                        # without ever reaching the response.
                        "error_message": str(exc)[:300],
                    }
                },
            )

        return response
    finally:
        request_id_var.reset(token)


def _with_names(db, rows: list[PublishLog]) -> list[PublishLogOut]:
    """Attach brand and channel names so the UI never renders a raw id."""
    account_ids = {r.account_id for r in rows}
    channel_ids = {r.channel_id for r in rows}

    accounts = {
        a.id: a.name
        for a in db.scalars(select(Account).where(Account.id.in_(account_ids))).all()
    } if account_ids else {}
    channels = {
        c.id: c.display_name
        for c in db.scalars(select(Channel).where(Channel.id.in_(channel_ids))).all()
    } if channel_ids else {}

    return [
        PublishLogOut(
            **{
                **PublishLogOut.model_validate(row, from_attributes=True).model_dump(),
                "account_name": accounts.get(row.account_id),
                "channel_name": channels.get(row.channel_id),
            }
        )
        for row in rows
    ]


@router.get("/publish/history", response_model=PublishLogPage, dependencies=[ApiKeyDep])
def publish_history(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    account_id: Annotated[str | None, Query()] = None,
    channel_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    platform: Annotated[str | None, Query(max_length=32)] = None,
) -> PublishLogPage:
    """Publishing history. Backs the Posts and Calendar pages.

    Deliberately NOT filtered by channel or brand state: a post that really went
    out stays visible after its channel is archived or its brand disabled, so the
    audit trail never silently loses rows.
    """
    stmt = select(PublishLog)
    count_stmt = select(func.count()).select_from(PublishLog)

    if account_id:
        stmt = stmt.where(PublishLog.account_id == account_id)
        count_stmt = count_stmt.where(PublishLog.account_id == account_id)
    if channel_id:
        stmt = stmt.where(PublishLog.channel_id == channel_id)
        count_stmt = count_stmt.where(PublishLog.channel_id == channel_id)
    if status_filter:
        stmt = stmt.where(PublishLog.status == status_filter)
        count_stmt = count_stmt.where(PublishLog.status == status_filter)
    if platform:
        stmt = stmt.where(PublishLog.platform == platform.lower())
        count_stmt = count_stmt.where(PublishLog.platform == platform.lower())

    total = int(db.scalar(count_stmt) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(PublishLog.created_at.desc(), PublishLog.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )

    return PublishLogPage(
        items=_with_names(db, rows), total=total, limit=limit, offset=offset
    )


@router.delete("/publish/history/{log_id}", status_code=204, dependencies=[ApiKeyDep])
def delete_history_entry(log_id: int, db: DbDep) -> Response:
    """Remove a single history row.

    Local bookkeeping only. This deletes this application's record of an
    attempt; it does not delete, edit or cancel the post on Buffer. Nothing in
    the publishing path calls this, so an attempt can never erase its own row.
    """
    from app.services.errors import AppError, ErrorCode
    from app.services.publish_history import delete_history_entry as _delete

    if not _delete(db, log_id):
        raise AppError(ErrorCode.HISTORY_ENTRY_NOT_FOUND, 404, field="log_id")

    logger.info(
        "publish.history_deleted",
        extra={"context": {"log_id": log_id}},
    )
    return Response(status_code=204)


class HistoryClearOut(BaseModel):
    deleted: int


@router.delete("/publish/history", response_model=HistoryClearOut, dependencies=[ApiKeyDep])
def clear_publish_history(
    db: DbDep,
    account_id: Annotated[str | None, Query()] = None,
    channel_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    platform: Annotated[str | None, Query(max_length=32)] = None,
    before: Annotated[datetime | None, Query()] = None,
) -> HistoryClearOut:
    """Bulk-remove history. Requires at least one filter.

    Refusing an unscoped delete is deliberate: history is the only record this
    application keeps of what it tried to publish, and a stray request should
    not be able to wipe it.
    """
    from app.services.errors import AppError, ErrorCode
    from app.services.publish_history import clear_history

    if not any([account_id, channel_id, status_filter, platform, before]):
        raise AppError(ErrorCode.HISTORY_CLEAR_REQUIRES_FILTER, 400, field="filter")

    deleted = clear_history(
        db,
        account_id=account_id,
        channel_id=channel_id,
        status_filter=status_filter,
        platform=platform,
        before=before,
    )
    logger.info(
        "publish.history_cleared",
        extra={
            "context": {
                "deleted": deleted,
                "account_id": account_id,
                "channel_id": channel_id,
                "status": status_filter,
                "platform": platform,
            }
        },
    )
    return HistoryClearOut(deleted=deleted)


@router.get("/publish/history/duplicates", response_model=DuplicateHistoryReport, dependencies=[ApiKeyDep])
def publish_history_duplicates(db: DbDep) -> DuplicateHistoryReport:
    """Report existing duplicate history rows. Read-only.

    New publishes can no longer produce duplicates, but rows written before this
    fix are still in the table. This endpoint finds them so they can be reviewed
    and reconciled deliberately. It changes nothing.
    """
    from app.services.publish_history import duplicate_history_report

    groups = duplicate_history_report(db)
    return DuplicateHistoryReport(
        generated_at=datetime.now(timezone.utc),
        total_rows=int(db.scalar(select(func.count()).select_from(PublishLog)) or 0),
        duplicate_groups=groups,
        redundant_rows=sum(g["copies"] - 1 for g in groups),
    )