"""Campaign endpoints.

Publishing deliberately routes through the SAME `PublishService` the Quick
Publisher uses. There is no second Buffer path.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select

from app.api.deps import ApiKeyDep, DbDep, N8nDep, SettingsDep
from app.api.request_id import new_request_id
from app.core.config import get_settings
from app.core.logging import get_logger, request_id_var
from app.models.ai_entities import Campaign, CampaignPost
from app.models.entities import Account, Channel
from app.schemas.ai import (
    BulkPublishRequest,
    BulkResult,
    BulkScheduleOverride,
    BulkScheduleRequest,
    CampaignCreate,
    CampaignListItem,
    CampaignOut,
    CampaignPage,
    CampaignPostOut,
    CampaignUpdate,
    GenerateRequest,
    GenerationStatus,
    PostActionResult,
    PostPublishRequest,
    PostScheduleRequest,
    PostUpdate,
    RegenerateRequest,
)
from app.schemas.publishing import MediaItem, PublishRequest
from app.services import account_service
from app.services.campaign_service import CampaignService
from app.services.errors import AppError, ErrorCode, USER_MESSAGES
from app.services.platform_limits import (
    media_capabilities,
    platform_label,
    publishing_capability,
    spec_for,
    validate_media_for_platform,
)
from app.services.publish_service import PublishService
from app.services.n8n_service import N8nService

logger = get_logger("app.ai.api")
router = APIRouter(prefix="/ai", tags=["ai-campaigns"])


# --------------------------------------------------------------------------- #
# serialisation
# --------------------------------------------------------------------------- #
def _post_out(post: CampaignPost, db: DbDep) -> CampaignPostOut:
    account = db.get(Account, post.account_id)
    channel = db.get(Channel, post.channel_id)
    effective = CampaignService.effective_content(post)

    def _json_list(raw: str | None) -> list:
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    try:
        media = json.loads(post.media_json) if post.media_json else []
        if not isinstance(media, list):
            media = []
    except (json.JSONDecodeError, TypeError):
        media = []

    spec = spec_for(post.platform)
    return CampaignPostOut(
        id=post.id,
        campaign_id=post.campaign_id,
        account_id=post.account_id,
        channel_id=post.channel_id,
        platform=post.platform,
        platform_label=platform_label(post.platform),
        account_name=account.name if account else post.account_id,
        generation_status=post.generation_status,
        publishing_status=post.publishing_status,
        generated_content=post.generated_content,
        edited_content=post.edited_content,
        effective_content=effective,
        hashtags=_json_list(post.hashtags),
        media_idea=post.media_idea,
        media=media,
        model_notes=post.model_notes,
        validation_warnings=_json_list(post.validation_warnings),
        is_edited=post.is_edited,
        has_previous_version=bool(post.previous_content),
        scheduled_at=post.scheduled_at,
        timezone=post.timezone,
        provider_id=post.provider_id,
        model=post.model,
        error_code=post.error_code,
        error_message=post.error_message,
        publication_id=post.publication_id,
        external_link=post.external_link,
        character_count=len(effective),
        character_limit=spec.limit,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


def _campaign_out(campaign: Campaign, db: DbDep) -> CampaignOut:
    posts = list(
        db.scalars(
            select(CampaignPost)
            .where(CampaignPost.campaign_id == campaign.id)
            .order_by(CampaignPost.account_id, CampaignPost.channel_id)
        ).all()
    )
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        topic=campaign.topic,
        objective=campaign.objective,
        generate_images=campaign.generate_images,
        source=campaign.source,
        status=campaign.status,
        language=campaign.language,
        provider_id=campaign.provider_id,
        model=campaign.model,
        target_count=len(posts),
        generated_count=sum(1 for p in posts if p.generation_status == "generated"),
        failed_count=sum(1 for p in posts if p.generation_status == "failed"),
        published_count=sum(1 for p in posts if p.publishing_status == "published"),
        scheduled_count=sum(1 for p in posts if p.publishing_status == "scheduled"),
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        posts=[_post_out(p, db) for p in posts],
    )


def _service(db: DbDep, settings: SettingsDep) -> CampaignService:
    return CampaignService(db, settings)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED, dependencies=[ApiKeyDep])
def create_campaign(payload: CampaignCreate, db: DbDep, settings: SettingsDep) -> CampaignOut:
    service = _service(db, settings)
    campaign = service.create(
        payload.model_dump(exclude={"targets"}),
        [t.model_dump() for t in payload.targets],
    )
    return _campaign_out(campaign, db)


@router.get("/campaigns", response_model=CampaignPage, dependencies=[ApiKeyDep])
def list_campaigns(
    db: DbDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
status_filter: Annotated[str | None, Query(alias="status")] = None,
    source: Annotated[str | None, Query()] = None,
) -> CampaignPage:
    service = _service(db, settings)
    rows, total = service.list_campaigns(limit, offset, status_filter, source)

    items = []
    for campaign in rows:
        posts = service.posts_for(campaign.id)
        items.append(
            CampaignListItem(
                id=campaign.id,
                name=campaign.name,
                topic=campaign.topic,
                objective=campaign.objective,
                source=campaign.source,
                status=campaign.status,
                target_count=len(posts),
                generated_count=sum(1 for p in posts if p.generation_status == "generated"),
                failed_count=sum(1 for p in posts if p.generation_status == "failed"),
                published_count=sum(1 for p in posts if p.publishing_status == "published"),
                scheduled_count=sum(1 for p in posts if p.publishing_status == "scheduled"),
                created_at=campaign.created_at,
                updated_at=campaign.updated_at,
            )
        )
    return CampaignPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut, dependencies=[ApiKeyDep])
def get_campaign(campaign_id: str, db: DbDep, settings: SettingsDep) -> CampaignOut:
    service = _service(db, settings)
    return _campaign_out(service.get(campaign_id), db)


@router.patch("/campaigns/{campaign_id}", response_model=CampaignOut, dependencies=[ApiKeyDep])
def update_campaign(campaign_id: str, payload: CampaignUpdate, db: DbDep, settings: SettingsDep) -> CampaignOut:
    service = _service(db, settings)
    campaign = service.get(campaign_id)

    data = payload.model_dump(exclude_unset=True, exclude={"targets"})
    if data:
        campaign = service.update(campaign_id, data)
    if payload.targets is not None:
        campaign = service.set_targets(campaign_id, [t.model_dump() for t in payload.targets])

    return _campaign_out(campaign, db)


@router.delete("/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None, dependencies=[ApiKeyDep])
def delete_campaign(campaign_id: str, db: DbDep, settings: SettingsDep) -> Response:
    _service(db, settings).delete(campaign_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #
@router.post("/campaigns/{campaign_id}/generate", response_model=CampaignOut, dependencies=[ApiKeyDep])
def generate(campaign_id: str, payload: GenerateRequest, db: DbDep, settings: SettingsDep) -> CampaignOut:
    """Start background generation. Returns immediately; poll for progress."""
    service = _service(db, settings)
    service.start_generation(
        campaign_id,
        post_ids=payload.post_ids,
        provider_id=payload.provider_id,
        overwrite_edited=payload.overwrite_edited,
    )
    return _campaign_out(service.get(campaign_id), db)


@router.get("/campaigns/{campaign_id}/generation-status", response_model=GenerationStatus, dependencies=[ApiKeyDep])
def generation_status(campaign_id: str, db: DbDep, settings: SettingsDep) -> GenerationStatus:
    service = _service(db, settings)
    campaign = service.get(campaign_id)
    posts = service.posts_for(campaign_id)

    def count(*statuses: str) -> int:
        return sum(1 for p in posts if p.generation_status in statuses)

    return GenerationStatus(
        campaign_id=campaign_id,
        status=campaign.status,
        pending=count("pending"),
        generating=count("generating"),
        generated=count("generated"),
        failed=count("failed"),
        skipped=count("skipped"),
        published=sum(1 for p in posts if p.publishing_status == "published"),
        scheduled=sum(1 for p in posts if p.publishing_status == "scheduled"),
        total=len(posts),
    )


@router.post("/campaign-posts/{post_id}/regenerate", response_model=CampaignPostOut, dependencies=[ApiKeyDep])
def regenerate_post(post_id: str, payload: RegenerateRequest, db: DbDep, settings: SettingsDep) -> CampaignPostOut:
    """Regenerate exactly one post. Other posts are untouched."""
    service = _service(db, settings)
    post = service.get_post(post_id)

    if post.publishing_status in ("published", "scheduled"):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            400,
            field="post_id",
        )
    if post.is_edited and not payload.overwrite_edited:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            409,
            field="post_id",
        )

    service.start_generation(
        post.campaign_id,
        post_ids=[post.id],
        provider_id=payload.provider_id,
        overwrite_edited=payload.overwrite_edited,
    )
    return _post_out(service.get_post(post_id), db)


@router.get("/media-capabilities", dependencies=[ApiKeyDep])
def media_capabilities_for_platform(platform: str) -> dict:
    """What a platform accepts, so the UI never offers an impossible option."""
    return media_capabilities(platform)


@router.post("/campaign-posts/{post_id}/generate-image", response_model=CampaignPostOut, dependencies=[ApiKeyDep])
def generate_post_image(post_id: str, db: DbDep, settings: SettingsDep) -> CampaignPostOut:
    """Create an image for one post and attach it.

    Needed for platforms that refuse text-only posts, and useful when a
    caption is ready but has no visual yet.
    """
    service = _service(db, settings)
    post = service.generate_image(post_id, regenerate=True)
    return _post_out(post, db)


@router.patch("/campaign-posts/{post_id}", response_model=CampaignPostOut, dependencies=[ApiKeyDep])
def update_post(post_id: str, payload: PostUpdate, db: DbDep, settings: SettingsDep) -> CampaignPostOut:
    service = _service(db, settings)
    post = service.update_post(post_id, payload.model_dump(exclude_unset=True))
    return _post_out(post, db)


@router.post("/campaign-posts/{post_id}/restore", response_model=CampaignPostOut, dependencies=[ApiKeyDep])
def restore_post(post_id: str, db: DbDep, settings: SettingsDep) -> CampaignPostOut:
    """Undo the last edit, stepping back one version."""
    service = _service(db, settings)
    post = service.restore_previous(post_id)
    return _post_out(post, db)


@router.post(
    "/campaign-posts/{post_id}/discard-edits", response_model=CampaignPostOut, dependencies=[ApiKeyDep]
)
def discard_edits(post_id: str, db: DbDep, settings: SettingsDep) -> CampaignPostOut:
    """Discard every human edit and go back to the generated draft."""
    service = _service(db, settings)
    post = service.discard_edits(post_id)
    return _post_out(post, db)


@router.delete("/campaign-posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None, dependencies=[ApiKeyDep])
def remove_post(post_id: str, db: DbDep, settings: SettingsDep) -> Response:
    service = _service(db, settings)
    post = service.get_post(post_id)
    if post.publishing_status in ("published", "scheduled"):
        raise AppError(ErrorCode.INVALID_REQUEST, 400, field="post_id")
    db.delete(post)
    db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# publishing - reuses the existing PublishService
# --------------------------------------------------------------------------- #
def _sync_publishing_status(db, campaign_id: str) -> None:
    """Keep the campaign row honest about what has actually gone out.

    A campaign is 'published' only when every post has. Generation outcomes
    ('ready' / 'partially_failed') are preserved otherwise, so publishing one
    post never hides a generation failure.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return

    rows = list(
        db.scalars(select(CampaignPost).where(CampaignPost.campaign_id == campaign_id)).all()
    )
    if not rows:
        return

    sent = sum(1 for r in rows if r.publishing_status in ("published", "scheduled"))
    generated_failed = sum(1 for r in rows if r.generation_status == "failed")

    if sent == len(rows):
        campaign.status = "published"
    elif sent:
        campaign.status = "partially_published"
    elif generated_failed:
        campaign.status = "partially_failed"
    db.commit()


def _build_publish_request(post: CampaignPost, campaign: Campaign, *, when: datetime | None, timezone: str) -> PublishRequest:
    content = CampaignService.effective_content(post)
    if not content.strip():
        raise AppError(ErrorCode.CONTENT_EMPTY, 400, field="content")

    try:
        media_raw = json.loads(post.media_json) if post.media_json else []
        if not isinstance(media_raw, list):
            media_raw = []
    except (json.JSONDecodeError, TypeError):
        media_raw = []

    # Only real, usable attachments travel. A half-filled media row must not
    # be forwarded to Buffer, or it is rejected as an invalid link.
    media = [
        MediaItem(**m)
        for m in media_raw
        if isinstance(m, dict) and str(m.get("url") or "").strip()
    ]

# Prevent a misleading success state for platforms Buffer will refuse.
    problems = validate_media_for_platform(post.platform, media)
    if problems:
        raise AppError(
            ErrorCode.MEDIA_INVALID,
            400,
            field="media",
            details=[{"reason": p} for p in problems],
        )

    is_schedule = when is not None
    return PublishRequest(
        account_id=post.account_id,
        channel_id=post.channel_id,
        content=content,
        media=media,
        publish_mode="SCHEDULE" if is_schedule else "NOW",
        scheduled_at=when,
        timezone=timezone,
        source="ai_campaign",
        campaign_id=campaign.id,
        ai_generated=True,
        content_id=post.id,
    )


def _publish_post(
    post: CampaignPost,
    campaign: Campaign,
    db: DbDep,
    n8n: N8nService,
    *,
    when: datetime | None,
    timezone: str,
    idempotency_key: str | None,
) -> PostActionResult:
    """Publish or schedule one post through the existing service."""
    if post.publishing_status in ("published", "scheduled"):
        # Guard here as well as via the idempotency key: a second click on
        # Publish must never queue the same post twice. Bulk callers catch
        # this per item and record it as a failed result.
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            409,
            field="post_id",
            details=[{"message": "This post has already been published or scheduled."}],
        )

    request_id = new_request_id()
    token = request_id_var.set(request_id)
    try:
        payload = _build_publish_request(post, campaign, when=when, timezone=timezone)
        service = PublishService(db, get_settings(), n8n)
        # Edited content wins: payload is built from effective_content above.
        response = service.publish(payload, request_id=request_id, idempotency_key=idempotency_key)

        post.publishing_status = "scheduled" if response.status == "scheduled" else "published"
        post.publication_id = response.buffer_publication_id
        post.external_link = response.external_link
        post.scheduled_at = response.scheduled_at if response.status == "scheduled" else None
        post.timezone = timezone
        post.error_code = None
        post.error_message = None
        post.publish_request_id = request_id
        db.commit()
        _sync_publishing_status(db, campaign.id)

        return PostActionResult(
            post_id=post.id,
            account_id=post.account_id,
            channel_id=post.channel_id,
            ok=True,
            status=response.status,
            publication_id=response.buffer_publication_id,
            external_link=response.external_link,
            scheduled_at=response.scheduled_at,
        )
    except AppError as exc:
        post.publishing_status = "failed"
        post.error_code = exc.code
        post.error_message = exc.user_message
        db.commit()
        return PostActionResult(
            post_id=post.id,
            account_id=post.account_id,
            channel_id=post.channel_id,
            ok=False,
            error_code=exc.code,
            error_message=exc.user_message,
        )
    finally:
        request_id_var.reset(token)


@router.post("/campaign-posts/{post_id}/publish", response_model=PostActionResult, dependencies=[ApiKeyDep])
def publish_post(
    post_id: str, payload: PostPublishRequest, db: DbDep, settings: SettingsDep, n8n: N8nDep
) -> PostActionResult:
    service = _service(db, settings)
    post = service.get_post(post_id)
    campaign = service.get(post.campaign_id)
    return _publish_post(
        post, campaign, db, n8n,
        when=None,
        timezone=post.timezone,
        idempotency_key=payload.idempotency_key or f"cmp-post-{post.id}-now",
    )


@router.post("/campaign-posts/{post_id}/schedule", response_model=PostActionResult, dependencies=[ApiKeyDep])
def schedule_post(
    post_id: str, payload: PostScheduleRequest, db: DbDep, settings: SettingsDep, n8n: N8nDep
) -> PostActionResult:
    service = _service(db, settings)
    post = service.get_post(post_id)
    campaign = service.get(post.campaign_id)
    return _publish_post(
        post, campaign, db, n8n,
        when=payload.scheduled_at,
        timezone=payload.timezone,
        idempotency_key=payload.idempotency_key or f"cmp-post-{post.id}-sched",
    )


@router.post("/campaign-posts/bulk-publish", response_model=BulkResult, dependencies=[ApiKeyDep])
def bulk_publish(
    payload: BulkPublishRequest, db: DbDep, settings: SettingsDep, n8n: N8nDep
) -> BulkResult:
    """Publish each selected post independently.

    One failure never invalidates the others.
    """
    if not payload.post_ids:
        # Silently succeeding here would let the UI claim "published 0" as a
        # successful bulk action.
        raise AppError(ErrorCode.AI_NO_CHANNELS_SELECTED, 400, field="post_ids")

    service = _service(db, settings)
    prefix = payload.idempotency_key_prefix or f"bulk-{secrets.token_hex(4)}"
    results: list[PostActionResult] = []

    for post_id in payload.post_ids:
        try:
            post = service.get_post(post_id)
            campaign = service.get(post.campaign_id)
            results.append(
                _publish_post(
                    post, campaign, db, n8n,
                    when=None,
                    timezone=post.timezone,
                    idempotency_key=f"{prefix}-{post_id}",
                )
            )
        except AppError as exc:
            # Keep the ids so the UI can attribute the failure to a row even
            # when the failure happened before the post could be loaded.
            failed_post = locals().get("post")
            results.append(
                PostActionResult(
                    post_id=post_id,
                    account_id=failed_post.account_id if failed_post else "",
                    channel_id=failed_post.channel_id if failed_post else "",
                    ok=False, error_code=exc.code, error_message=exc.user_message,
                )
            )

    succeeded = sum(1 for r in results if r.ok)
    return BulkResult(
        requested=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )


@router.post("/campaign-posts/bulk-schedule", response_model=BulkResult, dependencies=[ApiKeyDep])
def bulk_schedule(
    payload: BulkScheduleRequest, db: DbDep, settings: SettingsDep, n8n: N8nDep
) -> BulkResult:
    """Schedule selected posts. Per-post overrides are honoured."""
    service = _service(db, settings)
    prefix = payload.idempotency_key_prefix or f"bulk-{secrets.token_hex(4)}"
    overrides: dict[str, BulkScheduleOverride] = payload.overrides or {}
    results: list[PostActionResult] = []

    for post_id in payload.post_ids:
        when = payload.scheduled_at
        timezone = payload.timezone
        override = overrides.get(post_id)
        if override is not None:
            when = override.scheduled_at
            timezone = override.timezone or timezone

        try:
            post = service.get_post(post_id)
            campaign = service.get(post.campaign_id)
            results.append(
                _publish_post(
                    post, campaign, db, n8n,
                    when=when,
                    timezone=timezone,
                    idempotency_key=f"{prefix}-{post_id}",
                )
            )
        except AppError as exc:
            # Keep the ids so the UI can attribute the failure to a row even
            # when the failure happened before the post could be loaded.
            failed_post = locals().get("post")
            results.append(
                PostActionResult(
                    post_id=post_id,
                    account_id=failed_post.account_id if failed_post else "",
                    channel_id=failed_post.channel_id if failed_post else "",
                    ok=False, error_code=exc.code, error_message=exc.user_message,
                )
            )

    succeeded = sum(1 for r in results if r.ok)
    return BulkResult(
        requested=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )