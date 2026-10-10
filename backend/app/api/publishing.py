"""POST /api/publish and publishing history endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query
from sqlalchemy import func, select

from app.api.deps import ApiKeyDep, DbDep, N8nDep
from app.core.config import get_settings
from app.core.logging import get_logger, request_id_var
from app.models.entities import PublishLog
from app.schemas.publishing import PublishLogPage, PublishRequest, PublishResponse
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


@router.get("/publish/history", response_model=PublishLogPage, dependencies=[ApiKeyDep])
def publish_history(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    account_id: Annotated[str | None, Query()] = None,
    channel_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> PublishLogPage:
    """Publishing history. Backs the Posts and Calendar pages."""
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

    total = int(db.scalar(count_stmt) or 0)
    rows = db.scalars(
        stmt.order_by(PublishLog.created_at.desc(), PublishLog.id.desc()).limit(limit).offset(offset)
    ).all()

    return PublishLogPage(items=list(rows), total=total, limit=limit, offset=offset)