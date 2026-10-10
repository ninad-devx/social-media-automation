"""Campaign generation.

Design goals that drive the code:
  * one failed channel never erases successful results
  * each channel is independently retryable
  * bounded concurrency, so a 40-channel campaign does not 40 parallel requests
  * duplicate generation is prevented
  * no automatic fallback to another paid provider

Generation runs in a background thread and the frontend polls status, so the
page is never blocked.
"""
from __future__ import annotations

import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.ai_entities import (
    AIProvider,
    AccountInstructions,
    Campaign,
    CampaignPost,
    ChannelInstructions,
    GlobalInstructions,
)
from app.models.entities import Account, Channel
from app.services import account_service
from app.services.ai.base import GeneratedPost, ProviderError
from app.services.ai.image_service import (
    ImageGenerationError,
    image_prompt_for,
    render_card,
)
from app.services.ai_provider_service import ProviderService
from app.services.errors import AppError, ErrorCode
from app.services.platform_limits import limit_hint, requires_media, validate_platform_text
from app.services.prompt_service import build_system_prompt, build_user_prompt, effective_for_channel
from app.services.storage import MediaKind, build_storage

logger = get_logger("app.ai.campaigns")

# Runs started by this process, keyed by campaign id. Used for the quota guard
# and to stop double submissions.
_ACTIVE: dict[str, threading.Lock] = {}
_ACTIVE_LOCK = threading.Lock()
_GENERATION_TIMESTAMPS: list[datetime] = []


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _campaign_lock(campaign_id: str) -> threading.Lock:
    with _ACTIVE_LOCK:
        lock = _ACTIVE.get(campaign_id)
        if lock is None:
            lock = threading.Lock()
            _ACTIVE[campaign_id] = lock
        return lock


def _enforce_quota(settings: Settings) -> None:
    """Simple sliding-window guard against runaway generation."""
    with _ACTIVE_LOCK:
        cutoff = datetime.now(timezone.utc).timestamp() - 3600
        _GENERATION_TIMESTAMPS[:] = [t for t in _GENERATION_TIMESTAMPS if t.timestamp() > cutoff]
        if len(_GENERATION_TIMESTAMPS) >= settings.ai_generations_per_hour:
            raise AppError(ErrorCode.AI_QUOTA_EXCEEDED, 429)
        _GENERATION_TIMESTAMPS.append(datetime.now(timezone.utc))


class CampaignService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._providers = ProviderService(db, settings)

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def get(self, campaign_id: str) -> Campaign:
        campaign = self._db.get(Campaign, campaign_id)
        if campaign is None:
            raise AppError(ErrorCode.NOT_FOUND, 404, field="campaign_id")
        return campaign

    def list_campaigns(
        self,
        limit: int,
        offset: int,
        status: str | None = None,
        source: str | None = None,
    ) -> tuple[list[Campaign], int]:
        stmt = select(Campaign)
        count_stmt = select(func.count()).select_from(Campaign)
        if status:
            stmt = stmt.where(Campaign.status == status)
            count_stmt = count_stmt.where(Campaign.status == status)
        if source:
            # "quick_publish" and "ai_campaign"; "all" means no filter.
            if source != "all":
                stmt = stmt.where(Campaign.source == source)
                count_stmt = count_stmt.where(Campaign.source == source)
        total = int(self._db.scalar(count_stmt) or 0)
        rows = self._db.scalars(
            stmt.order_by(Campaign.created_at.desc()).limit(limit).offset(offset)
        ).all()
        return list(rows), total

    def posts_for(self, campaign_id: str) -> list[CampaignPost]:
        return list(
            self._db.scalars(
                select(CampaignPost)
                .where(CampaignPost.campaign_id == campaign_id)
                .order_by(CampaignPost.account_id, CampaignPost.channel_id)
            ).all()
        )

    def get_post(self, post_id: str) -> CampaignPost:
        post = self._db.get(CampaignPost, post_id)
        if post is None:
            raise AppError(ErrorCode.NOT_FOUND, 404, field="post_id")
        return post

    # ------------------------------------------------------------------ #
    # target resolution
    # ------------------------------------------------------------------ #
    def resolve_targets(self, targets: list[dict]) -> list[tuple[Account, Channel]]:
        """Validate every requested target through the existing rules.

        Reuses `account_service.resolve_channel`, so a channel that does not
        belong to the requested account is rejected exactly as it is in Quick
        Publish.
        """
        if not targets:
            raise AppError(ErrorCode.AI_NO_CHANNELS_SELECTED)

        if len(targets) > self._settings.ai_max_channels_per_campaign:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                400,
                field="targets",
            )

        seen: set[tuple[str, str]] = set()
        resolved: list[tuple[Account, Channel]] = []
        for target in targets:
            account_id = str(target.get("account_id") or "").strip()
            channel_id = str(target.get("channel_id") or "").strip()
            if not account_id or not channel_id:
                raise AppError(ErrorCode.INVALID_REQUEST, 400, field="targets")

            key = (account_id, channel_id)
            if key in seen:
                continue  # duplicate target, ignore rather than fail
            seen.add(key)

            # Raises CHANNEL_NOT_BELONG_TO_ACCOUNT etc. - same rules as publish.
            channel = account_service.resolve_channel(self._db, account_id, channel_id)
            account = account_service.get_active_account(self._db, account_id)
            resolved.append((account, channel))

        if not resolved:
            raise AppError(ErrorCode.AI_NO_CHANNELS_SELECTED)
        return resolved

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def create(self, data: dict, targets: list[dict]) -> Campaign:
        topic = (data.get("topic") or "").strip()
        if not topic:
            raise AppError(ErrorCode.AI_TOPIC_REQUIRED, 400, field="topic")

        # A hand-written draft supplies its own per-channel posts, so the
        # target list is derived from them rather than passed in separately.
        # De-duplicated because a campaign has at most one post per channel;
        # the last entry for a channel wins, which matches the UI, where
        # re-adding a channel replaces the existing row.
        raw_drafts = data.get("drafts") or []
        by_channel: dict[tuple[str, str], dict] = {}
        for d in raw_drafts:
            by_channel[(d["account_id"], d["channel_id"])] = d
        drafts = list(by_channel.values())
        if drafts:
            targets = [
                {"account_id": d["account_id"], "channel_id": d["channel_id"]} for d in drafts
            ]

        resolved = self.resolve_targets(targets)

        name = (data.get("name") or "").strip() or _auto_name(topic)
        campaign = Campaign(
            id=new_id("cmp"),
            name=name[:200],
            topic=topic,
            objective=(data.get("objective") or None),
            context=(data.get("context") or None),
            audience_override=(data.get("audience_override") or None),
            tone_override=(data.get("tone_override") or None),
            language=(data.get("language") or "en")[:16],
            campaign_instructions=(data.get("campaign_instructions") or None),
            cta=(data.get("cta") or None),
            keywords=(data.get("keywords") or None),
            avoid_claims=(data.get("avoid_claims") or None),
            generate_images=bool(data.get("generate_images")),
            source="quick_publish" if drafts else "ai_campaign",
            status="draft",
        )
        self._db.add(campaign)
        self._db.flush()

        self._ensure_post_rows(campaign, resolved, reset_content=False)

        # Attach the hand-written copy and media to each matching channel.
        if drafts:
            # The session does not autoflush, so the rows just added above are
            # not visible to a query until they are written out.
            self._db.flush()
            posts = list(
                self._db.scalars(
                    select(CampaignPost).where(CampaignPost.campaign_id == campaign.id)
                ).all()
            )
            by_channel = {(p.account_id, p.channel_id): p for p in posts}

            for draft in drafts:
                post = by_channel.get((draft["account_id"], draft["channel_id"]))
                if post is None:
                    continue
                post.generated_content = str(draft["content"]).strip()
                post.generation_status = "generated"
                if draft.get("media"):
                    post.media_json = json.dumps(draft["media"])
                if draft.get("hashtags"):
                    post.hashtags = json.dumps(
                        [str(t).lstrip("#").strip() for t in draft["hashtags"] if str(t).strip()]
                    )

        self._db.commit()

        logger.info(
            "ai.campaign_created",
            extra={"context": {"campaign_id": campaign.id, "targets": len(resolved)}},
        )
        return campaign

    def update(self, campaign_id: str, data: dict) -> Campaign:
        campaign = self.get(campaign_id)
        for field in (
            "name", "topic", "objective", "context", "audience_override",
            "tone_override", "language", "campaign_instructions", "cta",
            "keywords", "avoid_claims",
        ):
            if field in data and data[field] is not None:
                setattr(campaign, field, str(data[field]).strip() or None)
        if data.get("topic") is not None and not str(data["topic"]).strip():
            raise AppError(ErrorCode.AI_TOPIC_REQUIRED, 400, field="topic")
        self._db.commit()
        return campaign

    def delete(self, campaign_id: str) -> None:
        campaign = self.get(campaign_id)
        self._db.execute(delete(CampaignPost).where(CampaignPost.campaign_id == campaign.id))
        self._db.delete(campaign)
        self._db.commit()

    def _ensure_post_rows(
        self,
        campaign: Campaign,
        resolved: list[tuple[Account, Channel]],
        *,
        reset_content: bool,
    ) -> None:
        """Create missing post rows. Existing edits are never clobbered."""
        existing = {
            (p.account_id, p.channel_id): p for p in self.posts_for(campaign.id)
        }
        wanted: set[tuple[str, str]] = set()

        for account, channel in resolved:
            key = (account.id, channel.id)
            wanted.add(key)
            post = existing.get(key)
            if post is None:
                self._db.add(
                    CampaignPost(
                        id=new_id("cmpp"),
                        campaign_id=campaign.id,
                        account_id=account.id,
                        channel_id=channel.id,
                        platform=channel.platform,
                        generation_status="pending",
                        publishing_status="draft",
                        timezone=self._settings.default_timezone,
                    )
                )
            elif reset_content:
                post.generation_status = "pending"
                post.error_code = None
                post.error_message = None

        for key, post in existing.items():
            if key not in wanted:
                # No longer targeted: drop it unless it was already published.
                if post.publishing_status in ("draft", "failed"):
                    self._db.delete(post)

    def set_targets(self, campaign_id: str, targets: list[dict]) -> Campaign:
        campaign = self.get(campaign_id)
        resolved = self.resolve_targets(targets)
        self._ensure_post_rows(campaign, resolved, reset_content=False)
        self._db.commit()
        return campaign

    # ------------------------------------------------------------------ #
    # generation
    # ------------------------------------------------------------------ #
    def start_generation(
        self,
        campaign_id: str,
        post_ids: list[str] | None = None,
        provider_id: str | None = None,
        *,
        overwrite_edited: bool = False,
    ) -> Campaign:
        """Kick off background generation and return immediately."""
        campaign = self.get(campaign_id)
        lock = _campaign_lock(campaign_id)

        if not lock.acquire(blocking=False):
            raise AppError(ErrorCode.DUPLICATE_SUBMISSION, 409)

        try:
            _enforce_quota(self._settings)

            provider = self._providers.get(provider_id) if provider_id else self._providers.get_default()
            if provider is None:
                raise AppError(ErrorCode.AI_NO_PROVIDER, 400)
            if not provider.enabled:
                raise AppError(ErrorCode.AI_PROVIDER_DISABLED, 400, field="provider_id")

            targets = self.posts_for(campaign_id)
            if post_ids:
                wanted = set(post_ids)
                targets = [p for p in targets if p.id in wanted]

            pending = []
            for post in targets:
                if post.publishing_status in ("published", "scheduled"):
                    continue  # never regenerate something already sent
                if post.is_edited and not overwrite_edited:
                    # Skip silently; the API surfaces this as "skipped".
                    post.generation_status = "skipped"
                    post.error_message = "Skipped: this post was edited. Regenerate with overwrite to replace it."
                    continue
                if overwrite_edited and post.is_edited:
                    # The caller explicitly asked for the human copy to be
                    # replaced, so drop it now rather than leaving a stale
                    # edit shadowing the fresh generation.
                    post.edited_content = None
                    post.previous_content = None
                    post.is_edited = False
                post.generation_status = "pending"
                post.error_code = None
                post.error_message = None
                pending.append(post)

            if not pending:
                self._db.commit()
                lock.release()
                raise AppError(ErrorCode.AI_NO_CHANNELS_SELECTED, 400)

            campaign.status = "generating"
            campaign.provider_id = provider.id
            campaign.model = provider.model
            campaign.generation_runs += 1
            self._db.commit()

            post_ids_to_run = [p.id for p in pending]

            thread = threading.Thread(
                target=self._run_generation,
                args=(campaign.id, post_ids_to_run, provider.id),
                daemon=True,
                name=f"campaign-{campaign.id[:12]}",
            )
            thread.start()
            return campaign
        except Exception:
            lock.release()
            raise

    def _run_generation(self, campaign_id: str, post_ids: list[str], provider_id: str) -> None:
        """Runs off the request thread. Owns its own DB session."""
        from app.database.session import session_scope

        campaign_lock = _campaign_lock(campaign_id)
        try:
            with session_scope() as db:
                settings = self._settings
                providers = ProviderService(db, settings)
                adapter = providers.adapter_for_campaign(provider_id)

                provider = db.get(AIProvider, provider_id)
                model_name = provider.model if provider else None

                campaign = db.get(Campaign, campaign_id)
                global_instructions = db.get(GlobalInstructions, 1)

                rows = list(
                    db.scalars(select(CampaignPost).where(CampaignPost.id.in_(post_ids))).all()
                )

                # Mark all as generating before any network call.
                for row in rows:
                    row.generation_status = "generating"
                db.commit()

                max_workers = max(1, min(settings.ai_max_concurrency, len(rows)))

                def work(post_id: str) -> tuple[str, GeneratedPost | None, ProviderError | None]:
                    """One channel. Fully isolated: never raises."""
                    with session_scope() as inner:
                        row = inner.get(CampaignPost, post_id)
                        if row is None:
                            return post_id, None, None
                        try:
                            generated = self._generate_one(
                                inner, row, campaign, global_instructions, adapter, model_name
                            )
                            return post_id, generated, None
                        except ProviderError as exc:
                            return post_id, None, exc
                        except Exception as exc:  # defensive isolation
                            logger.warning(
                                "ai.channel_unexpected_error",
                                extra={"context": {"post_id": post_id, "error_type": type(exc).__name__}},
                            )
                            return post_id, None, ProviderError("Unexpected generation error.")

                succeeded: set[str] = set()
                failed: set[str] = set()

                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {pool.submit(work, pid): pid for pid in post_ids}
                    for future in as_completed(futures):
                        post_id, generated, error = future.result()
                        with session_scope() as inner:
                            row = inner.get(CampaignPost, post_id)
                            if row is None:
                                continue
                            if generated is not None:
                                row.generated_content = generated.post_text
                                row.hashtags = json.dumps(generated.hashtags)
                                row.media_idea = generated.media_idea
                                row.model_notes = generated.notes
                                row.validation_warnings = json.dumps(generated.warnings)
                                row.generation_status = "generated"
                                row.is_edited = False
                                row.previous_content = None
                                row.error_code = None
                                row.error_message = None
                                row.model = model_name
                                row.provider_id = provider_id
                                # Platform validation warnings surface as notes.
                                issues = validate_platform_text(row.platform, generated.post_text)
                                if issues:
                                    existing = generated.warnings or []
                                    row.validation_warnings = json.dumps(existing + issues)
                                inner.commit()
                                # Instagram and similar cannot publish a
                                # text-only post, so a draft without media is
                                # a draft that can never go out. Give it one.
                                if requires_media(row.platform) or (campaign and campaign.generate_images):
                                    self._ensure_image(inner, post_id, campaign)
                                succeeded.add(post_id)
                            elif error is not None:
                                row.generation_status = "failed"
                                row.error_code = error.code
                                row.error_message = error.message
                                failed.add(post_id)
                            inner.commit()

                # Campaign rollup
                with session_scope() as inner:
                    campaign = inner.get(Campaign, campaign_id)
                    if campaign is not None:
                        all_rows = list(
                            inner.scalars(
                                select(CampaignPost).where(CampaignPost.campaign_id == campaign_id)
                            ).all()
                        )
                        generated_n = sum(1 for r in all_rows if r.generation_status == "generated")
                        failed_n = sum(1 for r in all_rows if r.generation_status == "failed")
                        if failed_n and generated_n:
                            campaign.status = "partially_failed"
                        elif failed_n:
                            campaign.status = "partially_failed"
                        else:
                            campaign.status = "ready"
                        inner.commit()

                logger.info(
                    "ai.generation_finished",
                    extra={
                        "context": {
                            "campaign_id": campaign_id,
                            "requested": len(post_ids),
                            "succeeded": len(succeeded),
                            "failed": len(failed),
                        }
                    },
                )
        except Exception as exc:
            logger.error(
                "ai.generation_crashed",
                extra={"context": {"campaign_id": campaign_id, "error_type": type(exc).__name__}},
            )
        finally:
            try:
                campaign_lock.release()
            except RuntimeError:
                pass

    def _generate_one(
        self,
        db: Session,
        row: CampaignPost,
        campaign: Campaign | None,
        global_instructions: GlobalInstructions | None,
        adapter,
        model_name: str | None,
    ) -> GeneratedPost:
        account = db.get(Account, row.account_id)
        channel = db.get(Channel, row.channel_id)
        if account is None or channel is None:
            raise ProviderError("The account or channel for this post no longer exists.")

        account_instructions = db.get(AccountInstructions, account.id)
        channel_instructions = db.get(ChannelInstructions, channel.id)

        # Only THIS account's context is loaded. No cross-brand leakage.
        effective = effective_for_channel(account_instructions, channel_instructions, global_instructions)

        from app.services.platform_limits import platform_label

        label = platform_label(channel.platform)
        system_prompt = build_system_prompt(
            effective, account, channel, label, campaign, limit_hint(label)
        )
        user_prompt = build_user_prompt(campaign)
        return adapter.generate_structured(system_prompt, user_prompt)

    # ------------------------------------------------------------------ #
    # quick publish bridge
    # ------------------------------------------------------------------ #
    def record_quick_publish(
        self,
        *,
        content: str,
        targets: list[dict],
        media: list[dict],
        publish_response,
    ) -> Campaign:
        """Mirror a Quick Publish post into the campaign list.

        Quick Publish and AI campaigns are the same thing going to different
        audiences, so both live in one place: every post can be reviewed,
        given media, rescheduled or looked up later regardless of how it was
        created.
        """
        if not targets:
            return None

        resolved = self.resolve_targets(targets)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        campaign = Campaign(
            id=new_id("cmp"),
            name=f"Quick post · {day}",
            topic=(content.strip().split("\n")[0] or "Quick Publish")[:200],
            status="published",
            language="en",
            source="quick_publish",
        )
        self._db.add(campaign)
        self._db.flush()

        for account, channel in resolved:
            post = CampaignPost(
                id=new_id("cmpp"),
                campaign_id=campaign.id,
                account_id=account.id,
                channel_id=channel.id,
                platform=channel.platform,
                generated_content=content,
                generation_status="generated",
                publishing_status="published",
                publication_id=getattr(publish_response, "buffer_publication_id", None),
                external_link=getattr(publish_response, "external_link", None),
                timezone=getattr(publish_response, "timezone", None),
            )
            if media:
                post.media_json = json.dumps(media)
            self._db.add(post)

        self._db.commit()
        logger.info(
            "ai.quick_publish_recorded",
            extra={"context": {"campaign_id": campaign.id, "targets": len(resolved)}},
        )
        return campaign

    def _ensure_image(self, db: Session, post_id: str, campaign: Campaign | None) -> None:
        """Attach media to a post that cannot publish without it.

        Runs on the background thread's own session, and never raises: a
        missing image must not mark a perfectly good caption as failed.
        """
        import io

        try:
            row = db.get(CampaignPost, post_id)
            if row is None or row.media_json:
                return
            content = self.effective_content(row)
            account = db.get(Account, row.account_id)

            result = render_card(
                headline=self._headline_for(row, content),
                caption=content,
                brand=account.name if account else "",
            )
            stored = build_storage(self._settings).save(
                io.BytesIO(result.data),
                MediaKind("image", result.mime_type, "png"),
                f"{row.id}.png",
                len(result.data),
            )
            row.media_json = json.dumps(
                [
                    {
                        "type": "image",
                        "url": stored.public_url,
                        "alt_text": self._headline_for(row, content)[:120],
                    }
                ]
            )
            db.commit()
            logger.info(
                "ai.image_attached",
                extra={"context": {"post_id": post_id, "source": result.source}},
            )
        except Exception as exc:  # noqa: BLE001 - never fail generation here
            logger.warning(
                "ai.image_attach_failed",
                extra={"context": {"post_id": post_id, "error_type": type(exc).__name__}},
            )

    # ------------------------------------------------------------------ #
    # images
    # ------------------------------------------------------------------ #
    def generate_image(self, post_id: str, *, regenerate: bool = False) -> CampaignPost:
        """Attach a real image to one post.

        The provider's image endpoint is tried first. When it is unavailable -
        which is common, since most text-capable keys cannot reach an image
        model - the post's own copy is rendered into a designed card. Either
        way the post ends up with media, which is what platforms like
        Instagram need in order to publish at all.
        """
        import io

        post = self.get_post(post_id)
        if post.publishing_status in ("published", "scheduled"):
            raise AppError(ErrorCode.INVALID_REQUEST, 409, field="post_id")
        if post.media_json and not regenerate:
            raise AppError(ErrorCode.INVALID_REQUEST, 400, field="post_id")

        content = self.effective_content(post)
        campaign = self.get(post.campaign_id)
        channel = self._db.get(Channel, post.channel_id)
        account = self._db.get(Account, post.account_id)

        from app.services.platform_limits import platform_label

        prompt = image_prompt_for(
            campaign.topic, content, platform_label(post.platform)
        )

        result = None
        try:
            adapter = ProviderService(self._db, self._settings).adapter_for_campaign(
                post.provider_id
            )
            api_key = getattr(adapter, "api_key", None)
            base_url = getattr(adapter, "base_url", None)
            # Only worth trying on an OpenAI-shaped endpoint. Google requires
            # OAuth for its image models and answers API-key auth with a 401.
            if api_key and base_url and "googleapis" not in base_url:
                from app.services.ai.image_service import generate_via_provider

                result = generate_via_provider(
                    api_key=api_key,
                    base_url=base_url,
                    model=getattr(adapter, "image_model", None) or "gpt-image-1",
                    prompt=prompt,
                    timeout=min(self._settings.ai_timeout_seconds * 2, 120.0),
                )
        except (ProviderError, ImageGenerationError, AppError) as exc:
            # A missing image model is expected, not a failure. Fall through to
            # the card so the user still gets usable media.
            logger.info(
                "ai.image_provider_unavailable",
                extra={"context": {"post_id": post_id, "error_code": getattr(exc, "code", type(exc).__name__)}},
            )
            result = None

        if result is None:
            result = render_card(
                headline=self._headline_for(post, content),
                caption=content,
                brand=account.name if account else "",
            )

        storage = build_storage(self._settings)
        stored = storage.save(
            io.BytesIO(result.data),
            MediaKind("image", result.mime_type, "png"),
            f"{post.id}.png",
            len(result.data),
        )

        existing = []
        if post.media_json and regenerate:
            try:
                existing = [m for m in json.loads(post.media_json) if isinstance(m, dict)]
            except (json.JSONDecodeError, TypeError):
                existing = []
        existing.append(
            {"type": "image", "url": stored.public_url, "alt_text": self._headline_for(post, content)[:120]}
        )
        post.media_json = json.dumps(existing)
        post.media_idea = post.media_idea or (result.prompt_used if result.source == "card" else None)
        self._db.commit()
        return post

    @staticmethod
    def _headline_for(post: CampaignPost, content: str) -> str:
        """A short, punchy line for the image.

        The model's own media_idea is used when it gave one; otherwise the
        first sentence of the caption, trimmed to something that fits.
        """
        idea = (post.media_idea or "").strip()
        if idea:
            return idea.split("\n")[0][:110]

        first = (content or "").strip().split("\n")[0]
        if not first:
            return "New update"
        # Cut back to a clean word boundary rather than mid-word.
        if len(first) > 110:
            first = first[:110].rsplit(" ", 1)[0]
        return first.rstrip(".,;:!?")

    # ------------------------------------------------------------------ #
    # editing
    # ------------------------------------------------------------------ #
    def update_post(self, post_id: str, data: dict) -> CampaignPost:
        post = self.get_post(post_id)
        if post.publishing_status in ("published", "scheduled"):
            # Once it is out there, the copy is the record of what was sent.
            raise AppError(ErrorCode.INVALID_REQUEST, 409, field="post_id")
        if "content" in data and data["content"] is not None:
            content = str(data["content"])
            if not content.strip():
                raise AppError(ErrorCode.CONTENT_EMPTY, 400, field="content")
            # Save what the user is replacing, not the original generation,
            # so undo walks back through their own edits.
            previous = self.effective_content(post)
            if previous.strip() and previous != content:
                post.previous_content = previous
            post.edited_content = content
            post.is_edited = True

        if "hashtags" in data and data["hashtags"] is not None:
            tags = data["hashtags"]
            if isinstance(tags, list):
                post.hashtags = json.dumps([str(t).lstrip("#").strip() for t in tags if str(t).strip()])
            else:
                post.hashtags = json.dumps(
                    [t.lstrip("#").strip() for t in str(tags).split() if t.strip()]
                )

        if "media" in data and data["media"] is not None:
            post.media_json = json.dumps(data["media"])

        if "media_idea" in data and data["media_idea"] is not None:
            post.media_idea = str(data["media_idea"]).strip() or None

        self._db.commit()
        return post

    def restore_previous(self, post_id: str) -> CampaignPost:
        """Undo the most recent edit, stepping back one version."""
        post = self.get_post(post_id)
        if post.previous_content:
            post.edited_content = post.previous_content
            post.is_edited = True
            post.previous_content = None
            self._db.commit()
        return post

    def discard_edits(self, post_id: str) -> CampaignPost:
        """Throw away every human edit and go back to the generated draft."""
        post = self.get_post(post_id)
        post.edited_content = None
        post.previous_content = None
        post.is_edited = False
        self._db.commit()
        return post

    @staticmethod
    def effective_content(post: CampaignPost) -> str:
        """Edited copy always wins over the original generation."""
        if post.edited_content and post.edited_content.strip():
            return post.edited_content.strip()
        return (post.generated_content or "").strip()


def _auto_name(topic: str) -> str:
    clean = " ".join(topic.split())
    return clean[:80] if len(clean) > 80 else clean