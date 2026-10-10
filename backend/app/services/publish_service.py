"""Publishing orchestration.

Responsibilities, in order:
  1. validate the request
  2. resolve account + channel (and channel ownership)
  3. normalise content and schedule
  4. guard against duplicate submissions
  5. hand off to the publishing engine
  6. normalise whatever comes back
  7. persist a history row for the Posts page

Two engines, chosen by PUBLISH_ENGINE:
  "n8n"    the generic workflow performs the Buffer call (existing behaviour,
           unchanged default so a current install keeps working as-is);
  "direct" this service resolves the credential and calls Buffer itself.

Either way the checks in step 1-4 are identical, so switching engines cannot
weaken validation.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.entities import IdempotencyRecord, PublishLog
from app.schemas.publishing import PublishRequest, PublishResponse
from app.services import account_service
from app.services.errors import AppError, ErrorCode
from app.services.n8n_service import N8nResult, N8nService, build_n8n_payload, mapped_error_code

logger = get_logger("app.publish")

# Platforms that will not accept a text-only post. Mirrors the n8n config;
# checked here so the user gets an instant message instead of an upstream error.
MEDIA_REQUIRED_PLATFORMS = {"instagram"}


class PublishService:
    def __init__(self, db: Session, settings: Settings, n8n: N8nService) -> None:
        self._db = db
        self._settings = settings
        self._n8n = n8n

    # ------------------------------------------------------------------ #
    # validation
    # ------------------------------------------------------------------ #
    def _validate_content(self, content: str) -> str:
        if not isinstance(content, str):
            raise AppError(ErrorCode.MISSING_CONTENT, field="content")
        trimmed = content.strip()
        if not trimmed:
            raise AppError(ErrorCode.CONTENT_EMPTY, field="content")
        return trimmed

    def _validate_media(self, media: list, platform: str) -> list[dict]:
        if media is None:
            media = []
        if not isinstance(media, list):
            raise AppError(ErrorCode.MEDIA_INVALID, field="media")

        cleaned: list[dict] = []
        for index, item in enumerate(media):
            # Accept both Pydantic models (normal path) and plain dicts.
            data = item if isinstance(item, dict) else (
                item.model_dump() if hasattr(item, "model_dump") else None
            )
            if not isinstance(data, dict):
                raise AppError(ErrorCode.MEDIA_INVALID, field=f"media[{index}]")

            mtype = str(data.get("type", "")).lower()
            if mtype not in ("image", "video"):
                raise AppError(ErrorCode.MEDIA_INVALID, field=f"media[{index}].type")

            url = str(data.get("url", "")).strip()
            if not url.startswith(("http://", "https://")):
                raise AppError(ErrorCode.MEDIA_INVALID, field=f"media[{index}].url")

            entry = {"type": mtype, "url": url}
            if data.get("alt_text"):
                entry["alt_text"] = str(data["alt_text"])[:1000]
            cleaned.append(entry)

        if not cleaned and platform.lower() in MEDIA_REQUIRED_PLATFORMS:
            raise AppError(
                ErrorCode.MEDIA_INVALID,
                field="media",
            )

        return cleaned

    def _validate_schedule(self, request: PublishRequest) -> datetime | None:
        if request.publish_mode.value == "NOW":
            # A NOW publish never carries a schedule value.
            return None

        tz_name = request.timezone
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise AppError(ErrorCode.INVALID_TIMEZONE, field="timezone") from None

        if request.scheduled_at is None:
            raise AppError(ErrorCode.INVALID_SCHEDULE, field="scheduled_at")

        # The UI sends a naive local datetime, which is interpreted in the
        # request timezone. If an offset is present, respect it directly.
        if request.scheduled_at.tzinfo is None:
            local_dt = request.scheduled_at.replace(tzinfo=tz)
        else:
            local_dt = request.scheduled_at.astimezone(tz)

        target = local_dt.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)

        if target <= now:
            raise AppError(ErrorCode.SCHEDULE_IN_PAST, field="scheduled_at")

        horizon = now + timedelta(days=self._settings.max_schedule_days_ahead)
        if target > horizon:
            raise AppError(ErrorCode.SCHEDULE_TOO_FAR, field="scheduled_at")

        return target

    # ------------------------------------------------------------------ #
    # idempotency
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fingerprint(payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _replay(self, key: str, fingerprint: str) -> PublishResponse | None:
        record = self._db.get(IdempotencyRecord, key)
        if record is None:
            return None
        if record.state == "completed" and record.response_body:
            if record.request_fingerprint != fingerprint:
                raise AppError(
                    ErrorCode.INVALID_REQUEST,
                    status.HTTP_409_CONFLICT,
                )
            body = json.loads(record.response_body)
            return PublishResponse(**{**body, "idempotent_replay": True})
        # in_progress: a previous attempt never finished
        raise AppError(ErrorCode.DUPLICATE_SUBMISSION, status.HTTP_409_CONFLICT)

    # ------------------------------------------------------------------ #
    # engines
    # ------------------------------------------------------------------ #
    def _publish_via_buffer(
        self,
        *,
        request_id: str,
        account,
        channel,
        content: str,
        media: list[dict],
        publish_mode: str,
        scheduled_local: str | None,
        timezone_name: str,
    ) -> N8nResult:
        """Direct engine: resolve the credential here, then call Buffer.

        The token is decrypted in-process, used for one request, and discarded.
        It is never returned, logged, or handed to n8n.
        """
        from app.services.buffer_client import BufferApiError, platform_metadata_for
        from app.services.integration_service import IntegrationService, map_buffer_error

        try:
            service = IntegrationService(self._db, self._settings)
            client, buffer_channel_id = service.resolve_publish_target(channel)
        except AppError as exc:
            return N8nResult(
                success=False,
                upstream_code=exc.code.value if hasattr(exc.code, "value") else str(exc.code),
                upstream_message=exc.user_message,
            )

        try:
            result = client.create_post(
                channel_id=buffer_channel_id,
                text=content,
                media=media or None,
                scheduled_local=scheduled_local,
                timezone=timezone_name,
                source_suffix=request_id,
                platform_metadata=platform_metadata_for(channel.platform),
            )
        except BufferApiError as exc:
            logger.warning(
                "publish.buffer_error",
                extra={"context": {"request_id": request_id, "code": exc.code}},
            )
            return N8nResult(success=False, upstream_code=map_buffer_error(exc), upstream_message=exc.message)

        if not result.success:
            # Buffer accepted the request but failed it afterwards. Never
            # reported as a success.
            return N8nResult(
                success=False,
                upstream_code=ErrorCode.BUFFER_POST_FAILED,
                upstream_message=result.error_message,
            )

        scheduled_at = None
        if publish_mode == "SCHEDULE" and result.due_at:
            try:
                scheduled_at = datetime.fromisoformat(result.due_at.replace("Z", "+00:00"))
            except ValueError:
                scheduled_at = None

        return N8nResult(
            success=True,
            status="scheduled" if publish_mode == "SCHEDULE" else "published",
            buffer_publication_id=result.post_id,
            external_link=result.external_link,
            scheduled_at=scheduled_at,
            timezone=timezone_name if publish_mode == "SCHEDULE" else None,
            # Preserved so a single request stays traceable end to end.
            n8n_request_id=request_id,
            raw=result.raw,
        )

    # ------------------------------------------------------------------ #
    # main entry point
    # ------------------------------------------------------------------ #
    def _verify_media_is_reachable(self, media: list[dict]) -> None:
        """Refuse to publish media that Buffer could not download.

        Buffer fetches attachments itself after the post is created. An
        unreachable URL does not fail the request - the post simply arrives
        with no picture, which is the confusing symptom this prevents. The
        check costs one small ranged GET per attachment.
        """
        if not media:
            return

        from app.services.storage import verify_publicly_reachable

        ok, reason = verify_publicly_reachable([m["url"] for m in media])
        if not ok:
            raise AppError(ErrorCode.MEDIA_NOT_ACCESSIBLE, 400, field="media", details=[{"reason": reason}])

    def publish(
        self,
        request: PublishRequest,
        *,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> PublishResponse:
        started = time.perf_counter()

        account = account_service.get_active_account(self._db, request.account_id)
        channel = account_service.resolve_channel(self._db, request.account_id, request.channel_id)
        platform = channel.platform.lower()

        # Pre-flight on the integration, when this channel has one.
        #
        # Skipped for channels that predate integrations (integration_id is
        # null), so an existing install is completely unaffected. Once a channel
        # IS bound, a disabled or disconnected integration stops the publish
        # here with a precise reason instead of failing opaquely downstream.
        if channel.integration_id:
            from app.services.integration_service import IntegrationService

            IntegrationService(self._db, self._settings).resolve_publish_target(channel)

        content = self._validate_content(request.content)
        media = self._validate_media(request.media, platform)
        self._verify_media_is_reachable(media)
        scheduled_utc = self._validate_schedule(request)

        # What n8n expects: a naive local datetime plus the timezone it belongs to.
        n8n_scheduled_at = None
        if scheduled_utc is not None:
            n8n_scheduled_at = scheduled_utc.astimezone(ZoneInfo(request.timezone)).strftime("%Y-%m-%dT%H:%M:%S")

        payload = build_n8n_payload(
            {
                "account_id": request.account_id,
                "channel_id": request.channel_id,
                "content": content,
                "media": media,
                "publish_mode": request.publish_mode.value,
                "scheduled_at": n8n_scheduled_at,
                "timezone": request.timezone,
            }
        )
        fingerprint = self._fingerprint(payload)

        replay: PublishResponse | None = None
        record: IdempotencyRecord | None = None
        if idempotency_key:
            replay = self._replay(idempotency_key, fingerprint)
            if replay is not None:
                logger.info("publish.replay", extra={"context": {"request_id": request_id, "idempotency_key": idempotency_key}})
                return replay
            record = IdempotencyRecord(
                key=idempotency_key,
                request_fingerprint=fingerprint,
                state="in_progress",
            )
            self._db.add(record)
            self._db.commit()

        log_row = PublishLog(
            request_id=request_id,
            idempotency_key=idempotency_key,
            account_id=request.account_id,
            channel_id=request.channel_id,
            platform=platform,
            content=content,
            content_preview=content[:280],
            media_count=len(media),
            publish_mode=request.publish_mode.value,
            scheduled_at=scheduled_utc,
            timezone=request.timezone,
            status="pending",
            source=request.source or "dashboard",
        )
        self._db.add(log_row)

        result = (
            self._publish_via_buffer(
                request_id=request_id,
                account=account,
                channel=channel,
                content=content,
                media=media,
                publish_mode=request.publish_mode.value,
                scheduled_local=n8n_scheduled_at,
                timezone_name=request.timezone,
            )
            if self._settings.uses_direct_publishing
            else self._n8n.publish(payload)
        )
        duration_ms = int((time.perf_counter() - started) * 1000)

        if not result.success:
            code = mapped_error_code(result)
            log_row.status = "failed"
            log_row.error_code = code
            log_row.error_message = result.upstream_message
            log_row.n8n_request_id = result.n8n_request_id
            log_row.duration_ms = duration_ms
            self._db.commit()

            if record is not None:
                record.state = "failed"
                self._db.commit()

            logger.warning(
                "publish.failed",
                extra={
                    "context": {
                        "request_id": request_id,
                        "account_id": request.account_id,
                        "channel_id": request.channel_id,
                        "platform": platform,
                        "publish_mode": request.publish_mode.value,
                        "status": "failed",
                        "duration_ms": duration_ms,
                        "error_code": code,
                        "upstream_code": result.upstream_code,
                        "n8n_request_id": result.n8n_request_id,
                    }
                },
            )
            raise AppError(
                code,
                status.HTTP_502_BAD_GATEWAY,
                log_context={"request_id": request_id, "error_code": code},
            )

        status_value = "scheduled" if request.publish_mode.value == "SCHEDULE" else "published"
        log_row.status = status_value
        log_row.buffer_publication_id = result.buffer_publication_id
        log_row.external_link = result.external_link
        log_row.n8n_request_id = result.n8n_request_id
        log_row.duration_ms = duration_ms

        response = PublishResponse(
            success=True,
            request_id=request_id,
            status=status_value,
            account_id=request.account_id,
            channel_id=request.channel_id,
            platform=platform,
            account_name=account.name,
            channel_name=channel.display_name,
            buffer_publication_id=result.buffer_publication_id,
            external_link=result.external_link,
            scheduled_at=scheduled_utc if status_value == "scheduled" else None,
            timezone=request.timezone if status_value == "scheduled" else None,
            content_length=len(content),
            media_count=len(media),
            n8n_request_id=result.n8n_request_id,
        )

        if record is not None:
            record.state = "completed"
            record.response_status = 200
            record.response_body = response.model_dump_json()
            self._db.commit()
        else:
            self._db.commit()

        logger.info(
            "publish.success",
            extra={
                "context": {
                    "request_id": request_id,
                    "account_id": request.account_id,
                    "channel_id": request.channel_id,
                    "platform": platform,
                    "publish_mode": request.publish_mode.value,
                    "status": status_value,
                    "duration_ms": duration_ms,
                    "buffer_publication_id": result.buffer_publication_id,
                    "n8n_request_id": result.n8n_request_id,
                }
            },
        )
        return response