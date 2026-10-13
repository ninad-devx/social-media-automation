"""Internal service-to-service endpoint.

Used by the generic n8n workflow to perform the actual Buffer call. The point
is that the workflow never holds a Buffer token: it receives only internal ids
from the webhook, calls back here, and this service resolves the credential and
the external channel id itself.

Security:
  * disabled entirely unless INTERNAL_SERVICE_SECRET is configured;
  * requires a constant-time comparison of `X-Internal-Secret`;
  * reuses the ordinary channel-ownership and integration checks, so the
    workflow cannot reach a channel the requester was not entitled to.

This route deliberately sits OUTSIDE /api so the optional X-API-Key gate (which
protects the browser-facing app) does not apply to it - it has its own secret.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.database.session import get_db
from app.models.entities import PublishLog
from app.services import account_service
from app.services.buffer_client import BufferApiError
from app.services.errors import AppError, ErrorCode
from app.services.integration_service import IntegrationService, map_buffer_error
from app.services.platform_capabilities import buffer_metadata_for, validate_for_publish
from app.services.publish_history import confirm_publish_attempt

logger = get_logger("app.internal")

logger = get_logger("app.internal")

router = APIRouter(prefix="/internal", tags=["internal"])


def require_internal_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    x_internal_secret: Annotated[str | None, Header(alias="X-Internal-Secret")] = None,
) -> None:
    """Authenticate the n8n workflow.

    Fails closed: no configured secret means the endpoint does not exist.
    """
    expected = settings.internal_service_secret
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Internal endpoint is disabled.",
        )
    # Compare as bytes. hmac.compare_digest raises TypeError on str inputs that
    # are not ASCII, which would turn a bad header into a 500 instead of a 401.
    supplied = (x_internal_secret or "").encode("utf-8")
    if not hmac.compare_digest(supplied, expected.encode("utf-8")):
        logger.warning("internal.publish.unauthorized")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal service secret.",
        )


InternalAuth = Depends(require_internal_secret)


class InternalPublishRequest(BaseModel):
    """Exactly what the generic workflow sends back.

    Internal ids only. There is deliberately no field for a Buffer channel id
    or a token: the backend resolves both.
    """

    request_id: str = Field(min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=64)
    channel_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)
    publish_mode: str = Field(default="NOW", max_length=16)
    scheduled_at: str | None = None
    timezone: str | None = None
    media: list[dict[str, Any]] = Field(default_factory=list)
    # Explicit post type. Optional: when absent the platform default is used.
    post_type: str | None = Field(default=None, max_length=32)


@router.post("/publish", dependencies=[InternalAuth])
def internal_publish(
    payload: InternalPublishRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Resolve the integration and publish through Buffer.

    Returns the same normalized shape the webhook handler produces, so the
    workflow can pass it straight back to the original caller.
    """
    # Ownership and liveness checks, identical to the browser-facing path.
    account = account_service.get_active_account(db, payload.account_id)
    channel = account_service.resolve_channel(db, payload.account_id, payload.channel_id)

    service = IntegrationService(db, settings)
    client, buffer_channel_id = service.resolve_publish_target(channel)

    scheduled_local = None
    if payload.publish_mode.upper() == "SCHEDULE":
        scheduled_local = payload.scheduled_at
        if not scheduled_local:
            raise AppError(ErrorCode.INVALID_SCHEDULE, 400, field="scheduled_at")

    mode = "NOW" if payload.publish_mode.upper() == "NOW" else "SCHEDULE"

    # Re-validate here rather than trusting the caller: this route is a separate
    # entry point into the Buffer call, so the same capability rules must hold
    # here or they can be bypassed by going through n8n.
    resolved_type = validate_for_publish(
        platform=channel.platform,
        text=payload.content,
        media=payload.media or [],
        post_type=payload.post_type,
    )
    if resolved_type.requires_schedule and mode != "SCHEDULE":
        raise AppError(ErrorCode.INVALID_PUBLISH_MODE, 400, field="publish_mode")

    metadata = buffer_metadata_for(
        channel.platform, resolved_type, len(payload.media or [])
    )

    try:
        result = client.create_post(
            channel_id=buffer_channel_id,
            text=payload.content.strip(),
            media=payload.media or None,
            scheduled_local=scheduled_local,
            timezone=payload.timezone,
            source_suffix=payload.request_id,
            platform_metadata=metadata,
        )
    except BufferApiError as exc:
        # Never surfaced raw to the caller; mapped to a stable code.
        logger.warning(
            "internal.publish.buffer_error",
            extra={"context": {"request_id": payload.request_id, "code": exc.code}},
        )
        raise AppError(map_buffer_error(exc), status.HTTP_502_BAD_GATEWAY, field="channel_id") from exc

    if not result.success:
        raise AppError(
            ErrorCode.BUFFER_POST_FAILED,
            status.HTTP_502_BAD_GATEWAY,
            field="channel_id",
            details=[{"reason": result.error_message or "Buffer rejected the post."}],
        )

    status_value = "scheduled" if mode == "SCHEDULE" else "published"

    scheduled_at = None
    if status_value == "scheduled":
        scheduled_at = _parse_iso(result.due_at or scheduled_local)
        if scheduled_at is not None and scheduled_at.tzinfo is None:
            from zoneinfo import ZoneInfo

            try:
                scheduled_at = scheduled_at.replace(
                    tzinfo=ZoneInfo(payload.timezone or settings.default_timezone)
                )
            except Exception:  # pragma: no cover - invalid tz already validated upstream
                pass
        if scheduled_at is not None:
            scheduled_at = scheduled_at.astimezone(timezone.utc)

    # Record the confirmed outcome so history never shows a phantom success.
    #
    # This UPDATES the provisional row the browser-facing route already created
    # for this `request_id`, rather than inserting a second one. The two used to
    # write separate rows with different request ids, which is why every publish
    # appeared twice in history.
    confirm_publish_attempt(
        db,
        request_id=payload.request_id,
        status=status_value,
        account_id=account.id,
        channel_id=channel.id,
        platform=channel.platform,
        buffer_publication_id=result.post_id,
        external_link=result.external_link,
        scheduled_at=scheduled_at,
        source="n8n_internal",
        extra_fields={
            "media_count": len(payload.media or []),
            "publish_mode": mode,
            "content": payload.content.strip(),
            "content_preview": payload.content.strip()[:280],
            "timezone": payload.timezone or settings.default_timezone,
        },
    )

    return {
        "success": True,
        "request_id": payload.request_id,
        "status": status_value,
        "account_id": account.id,
        "account_name": account.name,
        "channel_id": channel.id,
        "channel_name": channel.display_name,
        "platform": channel.platform,
        "buffer_publication_id": result.post_id,
        "external_link": result.external_link,
        "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        "timezone": payload.timezone if status_value == "scheduled" else None,
        "content_length": len(payload.content.strip()),
        "media_count": len(payload.media or []),
    }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
