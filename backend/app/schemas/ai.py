"""Pydantic schemas for the AI feature.

Provider responses never contain secrets: the API only ever returns masked
metadata.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class ProviderOut(BaseModel):
    id: str
    provider_slug: str
    provider_name: str
    display_name: str
    base_url: str | None = None
    model: str
    organization: str | None = None
    project: str | None = None
    enabled: bool
    is_default: bool
    supports_model_discovery: bool
    requires_base_url: bool
    default_base_url: str | None = None
    default_model: str | None = None
    key_label: str
    help_url: str | None = None
    has_api_key: bool
    api_key_masked: str | None = None
    last_status: str | None = None
    last_error_code: str | None = None
    last_tested_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    encryption_available: bool = True


class ProviderCreate(BaseModel):
    provider_slug: str = Field(min_length=1, max_length=32)
    display_name: str | None = Field(default=None, max_length=120)
    api_key: str = Field(min_length=1, max_length=500)
    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=160)
    organization: str | None = Field(default=None, max_length=160)
    project: str | None = Field(default=None, max_length=160)
    enabled: bool = True
    make_default: bool = False


class ProviderUpdate(BaseModel):
    display_name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    organization: str | None = None
    project: str | None = None
    enabled: bool | None = None
    make_default: bool | None = None


class ProviderTestResult(BaseModel):
    ok: bool
    detail: str | None = None
    error_code: str | None = None
    tested_at: datetime | None = None


class ModelListOut(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list)
    detail: str | None = None
    error_code: str | None = None
    manual_entry_allowed: bool = True


class DetectProviderIn(BaseModel):
    """Probe a key before it is ever saved.

    The key is used for one live check and discarded. It is never written to
    the database and never returned.
    """

    api_key: str = Field(min_length=1)
    #: Optional. When the caller already knows the provider, skip detection.
    provider_slug: str | None = None
    base_url: str | None = None


class DetectProviderOut(BaseModel):
    detected_slug: str
    confident: bool
    reason: str
    display_name: str
    default_base_url: str
    default_model: str | None
    supports_model_discovery: bool
    requires_base_url: bool
    key_label: str
    help_url: str | None
    #: True when the live check against the provider succeeded.
    credentials_ok: bool
    detail: str | None = None
    error_code: str | None = None
    models: list[str] = Field(default_factory=list)


class ProviderCatalogueItem(BaseModel):
    slug: str
    display_name: str
    supports_model_discovery: bool
    requires_base_url: bool
    default_base_url: str | None = None
    default_model: str | None = None
    key_label: str
    help_url: str | None = None
    supports_organization: bool = False
    supports_project: bool = False


# --------------------------------------------------------------------------- #
# Instructions
# --------------------------------------------------------------------------- #
class GlobalInstructionsOut(BaseModel):
    content: str
    updated_at: datetime | None = None


class GlobalInstructionsIn(BaseModel):
    content: str = ""


class AccountInstructionsOut(BaseModel):
    account_id: str
    account_name: str
    description: str | None = None
    industry: str | None = None
    target_audience: str | None = None
    products: str | None = None
    voice: str | None = None
    goals: str | None = None
    preferred_cta: str | None = None
    terminology: str | None = None
    avoid_words: str | None = None
    hashtag_preferences: str | None = None
    language: str | None = None
    extra_context: str | None = None
    instructions: str | None = None
    configured: bool = False
    updated_at: datetime | None = None


class AccountInstructionsIn(BaseModel):
    description: str | None = None
    industry: str | None = None
    target_audience: str | None = None
    products: str | None = None
    voice: str | None = None
    goals: str | None = None
    preferred_cta: str | None = None
    terminology: str | None = None
    avoid_words: str | None = None
    hashtag_preferences: str | None = None
    language: str | None = None
    extra_context: str | None = None
    instructions: str | None = None


class ChannelInstructionsOut(BaseModel):
    channel_id: str
    account_id: str
    account_name: str
    platform: str
    platform_label: str
    display_name: str
    instructions: str | None = None
    configured: bool = False
    updated_at: datetime | None = None


class ChannelInstructionsIn(BaseModel):
    instructions: str | None = None


class InstructionLayerOut(BaseModel):
    """One instruction layer, in precedence order (later wins)."""

    key: str
    label: str
    rank: int
    configured: bool
    detail: str


class EffectiveInstructionsOut(BaseModel):
    channel_id: str
    account_id: str
    platform: str
    platform_label: str
    # Ordered weakest -> strongest, so the UI can render precedence directly.
    layers: list[InstructionLayerOut]
    text: str
    system_rules_preview: str


class TestGenerationIn(BaseModel):
    topic: str = Field(min_length=1)
    provider_id: str | None = None


class TestGenerationOut(BaseModel):
    ok: bool
    post_text: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    model: str | None = None
    error_code: str | None = None
    message: str | None = None


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #
class CampaignTarget(BaseModel):
    account_id: str
    channel_id: str


class CampaignDraftIn(BaseModel):
    """One hand-written post inside a draft campaign.

    Quick Publish composes a single post at a time, so a campaign assembled
    from it carries its own text and media per channel rather than a shared
    generated draft.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    account_id: str
    channel_id: str
    content: str = Field(min_length=1)
    media: list[dict] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # min_length alone would accept "   ", which produces a post that
        # looks empty and cannot be published.
        if not v.strip():
            raise ValueError("Post content cannot be blank.")
        return v


class CampaignCreate(BaseModel):
    name: str | None = None
    topic: str = Field(min_length=1)
    objective: str | None = None
    context: str | None = None
    audience_override: str | None = None
    tone_override: str | None = None
    language: str = "en"
    campaign_instructions: str | None = None
    cta: str | None = None
    keywords: str | None = None
    avoid_claims: str | None = None
    targets: list[CampaignTarget] = Field(default_factory=list)
    #: Create an image for every post, not just platforms that require media.
    generate_images: bool = False
    #: Hand-written per-channel posts. Present when the campaign is a draft
    #: built in Quick Publish rather than generated by a model.
    drafts: list[CampaignDraftIn] = Field(default_factory=list)


class CampaignUpdate(BaseModel):
    name: str | None = None
    topic: str | None = None
    objective: str | None = None
    context: str | None = None
    audience_override: str | None = None
    tone_override: str | None = None
    language: str | None = None
    campaign_instructions: str | None = None
    cta: str | None = None
    keywords: str | None = None
    avoid_claims: str | None = None
    targets: list[CampaignTarget] | None = None


class CampaignPostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    campaign_id: str
    account_id: str
    channel_id: str
    platform: str
    platform_label: str
    account_name: str
    generation_status: str
    publishing_status: str
    generated_content: str | None = None
    edited_content: str | None = None
    effective_content: str
    hashtags: list[str] = Field(default_factory=list)
    media_idea: str | None = None
    media: list[dict] = Field(default_factory=list)
    model_notes: str | None = None
    validation_warnings: list[str] = Field(default_factory=list)
    is_edited: bool = False
    has_previous_version: bool = False
    scheduled_at: datetime | None = None
    timezone: str
    provider_id: str | None = None
    model: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    publication_id: str | None = None
    external_link: str | None = None
    character_count: int = 0
    character_limit: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CampaignOut(BaseModel):
    id: str
    name: str
    topic: str
    objective: str | None = None
    generate_images: bool = False
    source: str = "ai_campaign"
    status: str
    language: str
    provider_id: str | None = None
    model: str | None = None
    target_count: int = 0
    generated_count: int = 0
    failed_count: int = 0
    published_count: int = 0
    scheduled_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    posts: list[CampaignPostOut] = Field(default_factory=list)


class CampaignListItem(BaseModel):
    id: str
    name: str
    topic: str
    objective: str | None = None
    source: str = "ai_campaign"
    status: str
    target_count: int
    generated_count: int
    failed_count: int
    published_count: int
    scheduled_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CampaignPage(BaseModel):
    items: list[CampaignListItem]
    total: int
    limit: int
    offset: int


class GenerateRequest(BaseModel):
    provider_id: str | None = None
    post_ids: list[str] | None = None
    overwrite_edited: bool = False


class GenerationStatus(BaseModel):
    campaign_id: str
    status: str
    pending: int = 0
    generating: int = 0
    generated: int = 0
    failed: int = 0
    skipped: int = 0
    published: int = 0
    scheduled: int = 0
    total: int = 0


class PostUpdate(BaseModel):
    content: str | None = None
    hashtags: list[str] | None = None
    media: list[dict] | None = None
    media_idea: str | None = None


class RegenerateRequest(BaseModel):
    provider_id: str | None = None
    overwrite_edited: bool = False


class PostPublishRequest(BaseModel):
    idempotency_key: str | None = None


class PostScheduleRequest(BaseModel):
    scheduled_at: datetime
    timezone: str = "Asia/Dhaka"
    idempotency_key: str | None = None


class BulkPublishRequest(BaseModel):
    post_ids: list[str]
    idempotency_key_prefix: str | None = None


class BulkScheduleRequest(BaseModel):
    post_ids: list[str]
    scheduled_at: datetime
    timezone: str = "Asia/Dhaka"
    # Optional per-post overrides, keyed by post id.
    overrides: dict[str, "BulkScheduleOverride"] | None = None
    idempotency_key_prefix: str | None = None


class BulkScheduleOverride(BaseModel):
    scheduled_at: datetime
    timezone: str | None = None


class PostActionResult(BaseModel):
    post_id: str
    account_id: str
    channel_id: str
    ok: bool
    status: str | None = None
    publication_id: str | None = None
    external_link: str | None = None
    scheduled_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


class BulkResult(BaseModel):
    requested: int
    succeeded: int
    failed: int
    results: list[PostActionResult]