"""AI feature tables.

Extends the existing database - no second datastore. Secrets are stored
encrypted (see `app.services.crypto`) and never serialised to the browser.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class AIProvider(TimestampMixin, Base):
    __tablename__ = "ai_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Registry slug, e.g. "openai", "anthropic", "openai_compatible".
    provider_slug: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # User-facing label, so several keys can point at the same provider.
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Fernet ciphertext. NEVER returned by the API.
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(160), nullable=True)
    project: Mapped[str | None] = mapped_column(String(160), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Connection probe results. No secrets, ever.
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ok | error
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Instructions: global / account / channel
# --------------------------------------------------------------------------- #
class GlobalInstructions(TimestampMixin, Base):
    """Singleton row (id=1) holding the application-wide writing rules."""

    __tablename__ = "ai_global_instructions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AccountInstructions(TimestampMixin, Base):
    """Brand profile + writing instructions for one account."""

    __tablename__ = "ai_account_instructions"
    __table_args__ = (Index("ix_ai_account_instructions_account", "account_id"),)

    account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    # Brand profile fields.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    products: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminology: Mapped[str | None] = mapped_column(Text, nullable=True)
    avoid_words: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtag_preferences: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Free-form extra context.
    extra_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Writing instructions for this account.
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChannelInstructions(TimestampMixin, Base):
    """Writing instructions for one specific channel of one account."""

    __tablename__ = "ai_channel_instructions"
    __table_args__ = (Index("ix_ai_channel_instructions_channel", "channel_id"),)

    channel_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True
    )
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #
class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    campaign_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    avoid_claims: Mapped[str | None] = mapped_column(Text, nullable=True)
    generate_images: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "ai_campaign" or "quick_publish". Lets the history view label and filter
    # posts by where they came from.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="ai_campaign")

    # draft | generating | ready | partially_failed | completed
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", index=True)
    # Provider used for the last generation, for traceability.
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Total requested generation attempts, used for the quota guard.
    generation_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CampaignPost(TimestampMixin, Base):
    __tablename__ = "campaign_posts"
    # Single-column indexes come from the column index=True flags plus the
    # naming convention. Only the composite uniqueness needs declaring.
    __table_args__ = (
        Index("uq_campaign_posts_campaign_channel", "campaign_id", "channel_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    # Real account/channel ids, so ownership stays enforced by the same rules
    # the Quick Publisher uses.
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)

    # Original AI output, always preserved.
    generated_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # User-edited copy. This - not generated_content - is what gets published.
    edited_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_idea: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON list of the existing media contract, so the Quick Publisher media
    # pipeline is reused rather than duplicated.
    media_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The format the author chose in Quick Publish (reel, story, carousel).
    #
    # Kept here rather than only on the publish log because a campaign post is
    # edited and republished long after it is created. Without it, the format
    # chosen at draft time was lost and the post fell back to a plain feed post.
    # Null means "use the platform default", which is what every row written
    # before this column existed means.
    post_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # pending | generating | generated | failed | skipped
    generation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # draft | published | scheduled | failed
    publishing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")

    # True once a human has touched the text, so regeneration can warn.
    is_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Retained previous text so an explicit regenerate is recoverable.
    previous_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Dhaka")

    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    publish_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)