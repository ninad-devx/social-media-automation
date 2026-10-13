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
from app.models.entities import IdempotencyRecord
from app.schemas.publishing import PublishRequest, PublishResponse
from app.services import account_service
from app.services.errors import AppError, ErrorCode
from app.services.n8n_service import N8nResult, N8nService, build_n8n_payload, mapped_error_code
from app.services.platform_capabilities import buffer_metadata_for, validate_for_publish
from app.services.publish_history import record_publish_attempt

logger = get_logger("app.publish")


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

        # Platform-specific "you must attach media" rules are enforced by the
        # shared capability model, not by a second hardcoded list. The removed
        # MEDIA_REQUIRED_PLATFORMS set only knew about Instagram, so Pinterest
        # and YouTube drafts passed validation and were rejected upstream.

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
        """Identity of the *content*, not of the attempt.

        `request_id` is deliberately excluded. It is different on every call, so
        including it made two byte-identical publishes look like two different
        payloads - which broke the replay path entirely and turned a safe
        double-submit into a hard 409.
        """
        identity = {k: v for k, v in payload.items() if k != "request_id"}
        canonical = json.dumps(identity, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _replay(self, key: str, fingerprint: str) -> PublishResponse | None:
        """Decide what an incoming Idempotency-Key means.

        The previous version replayed only `completed` and refused everything
        else, which meant a *failed* publish could never be retried: the
        deterministic keys the campaign UI uses (`cmp-post-<id>-now`) stayed
        wedged at DUPLICATE_SUBMISSION forever, so a post whose first attempt
        hit a transient error could never go out.

        The rules now:

        * ``completed`` + identical payload -> replay the stored response.
          Publishing twice is still impossible.
        * ``completed`` + different payload -> refused. A key must never be
          reused to quietly submit something different.
        * ``in_progress`` + still fresh -> refused. That is the concurrent
          double-submit guard, and it stays.
        * ``in_progress`` + older than the staleness window -> taken over. The
          previous process died holding the key; nothing was published.
        * ``failed`` -> retryable. Nothing was published, so trying again is the
          only sensible behaviour.

        Returns the stored response for a replay, or None when the caller should
        proceed. Raises :class:`AppError` when the key must be refused.
        """
        record = self._db.get(IdempotencyRecord, key)
        if record is None:
            return None

        if record.state == "completed":
            if record.request_fingerprint != fingerprint:
                raise AppError(
                    ErrorCode.IDEMPOTENCY_KEY_REUSED,
                    status.HTTP_409_CONFLICT,
                    details=[
                        {
                            "reason": (
                                "This idempotency key already completed with different "
                                "content. Use a new key to publish something else."
                            )
                        }
                    ],
                )
            if record.response_body:
                body = json.loads(record.response_body)
                return PublishResponse(**{**body, "idempotent_replay": True})
            return None

        if record.state == "in_progress":
            age = self._attempt_age_seconds(record)
            window = max(1, self._settings.idempotency_stale_after_seconds)
            if age < window:
                raise AppError(ErrorCode.DUPLICATE_SUBMISSION, status.HTTP_409_CONFLICT)
            logger.warning(
                "publish.idempotency_takeover",
                extra={
                    "context": {
                        "idempotency_key": key,
                        "age_seconds": age,
                        "window_seconds": window,
                    }
                },
            )

        # failed, or a stale in_progress we just took over: this key is free.
        record.request_fingerprint = fingerprint
        record.state = "in_progress"
        record.attempts = int(record.attempts or 1) + 1
        record.response_status = None
        record.response_body = None
        record.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        return None

    @staticmethod
    def _attempt_age_seconds(record: IdempotencyRecord) -> float:
        stamp = record.updated_at or record.created_at
        if stamp is None:
            return float("inf")
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp).total_seconds()

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
        resolved_type,
    ) -> N8nResult:
        """Direct engine: resolve the credential here, then call Buffer.

        The token is decrypted in-process, used for one request, and discarded.
        It is never returned, logged, or handed to n8n.
        """
        from app.services.buffer_client import BufferApiError
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
                platform_metadata=buffer_metadata_for(
                    channel.platform, resolved_type, len(media or [])
                ),
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

        # Eligibility, enforced here rather than trusted from the UI. Every
        # selector in the frontend showed archived, locked, disconnected and
        # unbound channels as if they were fine; the backend is the only place
        # that can be sure.
        verdict = account_service.eligibility(channel)
        if not verdict.publishable:
            raise AppError(
                verdict.reason_code or ErrorCode.CHANNEL_INACTIVE,
                status.HTTP_409_CONFLICT,
                field="channel_id",
                details=[{"reason": verdict.reason}],
            )

        # Pre-flight on the integration, when this channel has one. Gives the
        # precise reason (disabled integration, lost workspace, disconnected
        # profile) instead of a generic failure further down.
        if channel.integration_id:
            from app.services.integration_service import IntegrationService

            IntegrationService(self._db, self._settings).resolve_publish_target(channel)

        content = self._validate_content(request.content)
        media = self._validate_media(request.media, platform)

        # One capability model for every platform: post type resolution and the
        # media rules that go with it. This replaces the competing
        # MEDIA_REQUIRED_PLATFORMS set and the separate platform_limits copy.
        resolved_type = validate_for_publish(
            platform=platform,
            text=content,
            media=media,
            post_type=request.post_type,
        )

        self._verify_media_is_reachable(media)
        scheduled_utc = self._validate_schedule(request)

        if resolved_type.requires_schedule and scheduled_utc is None:
            raise AppError(
                ErrorCode.INVALID_PUBLISH_MODE,
                400,
                field="publish_mode",
                details=[
                    {
                        "reason": (
                            f"A {channel.display_name} {resolved_type.label.lower()} can only "
                            "be scheduled, not shared immediately."
                        )
                    }
                ],
            )

        # What n8n expects: a naive local datetime plus the timezone it belongs to.
        n8n_scheduled_at = None
        if scheduled_utc is not None:
            n8n_scheduled_at = scheduled_utc.astimezone(ZoneInfo(request.timezone)).strftime("%Y-%m-%dT%H:%M:%S")

        payload = build_n8n_payload(
            {
                "request_id": request_id,
                "account_id": request.account_id,
                "channel_id": request.channel_id,
                "content": content,
                "post_type": resolved_type.key,
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
            record = self._db.get(IdempotencyRecord, idempotency_key)
            if record is None:
                record = IdempotencyRecord(
                    key=idempotency_key,
                    request_fingerprint=fingerprint,
                    state="in_progress",
                    attempts=1,
                )
                self._db.add(record)
            self._db.commit()

        # One provisional row, created idempotently on request_id. When the n8n
        # engine is in use the callback at /internal/publish updates THIS row
        # with the confirmed outcome instead of inserting a second one.
        log_row = record_publish_attempt(
            self._db,
            request_id=request_id,
            account_id=request.account_id,
            channel_id=request.channel_id,
            platform=platform,
            content=content,
            media_count=len(media),
            publish_mode=request.publish_mode.value,
            scheduled_at=scheduled_utc,
            timezone_name=request.timezone,
            idempotency_key=idempotency_key,
            source=request.source or "dashboard",
        )
        log_row.post_type = resolved_type.key

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
                resolved_type=resolved_type,
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
                record.last_error_code = str(code)
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