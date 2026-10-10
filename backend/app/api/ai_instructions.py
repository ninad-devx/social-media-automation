"""Global / account / channel AI instructions, plus effective-instruction
preview and a single-shot generation test.
"""
from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy.orm import Session

from app.api.deps import ApiKeyDep, DbDep, SettingsDep
from app.core.logging import get_logger
from app.models.ai_entities import AccountInstructions, ChannelInstructions, GlobalInstructions
from app.models.entities import Account, Channel
from app.schemas.ai import (
    AccountInstructionsIn,
    AccountInstructionsOut,
    ChannelInstructionsIn,
    ChannelInstructionsOut,
    EffectiveInstructionsOut,
    GlobalInstructionsIn,
    GlobalInstructionsOut,
    InstructionLayerOut,
    TestGenerationIn,
    TestGenerationOut,
)
from app.services import account_service
from app.services.ai.base import ProviderError
from app.services.ai_provider_service import ProviderService
from app.services.errors import AppError, ErrorCode
from app.services.platform_limits import platform_label, spec_for
from app.services.prompt_service import (
    SYSTEM_RULES,
    build_system_prompt,
    build_user_prompt,
    effective_for_channel,
)

logger = get_logger("app.ai.instructions")
router = APIRouter(prefix="/ai", tags=["ai-instructions"])


# --------------------------------------------------------------------------- #
# global
# --------------------------------------------------------------------------- #
@router.get("/instructions", response_model=GlobalInstructionsOut, dependencies=[ApiKeyDep])
def get_global_instructions(db: DbDep) -> GlobalInstructionsOut:
    row = db.get(GlobalInstructions, 1)
    return GlobalInstructionsOut(
        content=row.content if row else "",
        updated_at=row.updated_at if row else None,
    )


@router.put("/instructions", response_model=GlobalInstructionsOut, dependencies=[ApiKeyDep])
def put_global_instructions(payload: GlobalInstructionsIn, db: DbDep) -> GlobalInstructionsOut:
    row = db.get(GlobalInstructions, 1)
    if row is None:
        row = GlobalInstructions(id=1, content=payload.content)
        db.add(row)
    else:
        row.content = payload.content
    db.commit()
    logger.info("ai.global_instructions_saved", extra={"context": {"length": len(payload.content)}})
    return GlobalInstructionsOut(content=row.content, updated_at=row.updated_at)


# --------------------------------------------------------------------------- #
# account / brand
# --------------------------------------------------------------------------- #
@router.get("/accounts/{account_id}/instructions", response_model=AccountInstructionsOut, dependencies=[ApiKeyDep])
def get_account_instructions(account_id: str, db: DbDep) -> AccountInstructionsOut:
    account = account_service.get_account(db, account_id)
    row = db.get(AccountInstructions, account_id)
    fields = (
        "description", "industry", "target_audience", "products", "voice",
        "goals", "preferred_cta", "terminology", "avoid_words",
        "hashtag_preferences", "language", "extra_context", "instructions",
    )
    data = {
        "account_id": account.id,
        "account_name": account.name,
        # Configured means ANY brand context is filled in, not just the
        # free-text box. Otherwise a user who set only "voice" would be
        # told they have no brand profile.
        "configured": bool(
            row and any((getattr(row, f, None) or "").strip() for f in fields)
        ),
        "updated_at": row.updated_at if row else None,
    }
    if row:
        for field in fields:
            data[field] = getattr(row, field)
    return AccountInstructionsOut(**data)


@router.put("/accounts/{account_id}/instructions", response_model=AccountInstructionsOut, dependencies=[ApiKeyDep])
def put_account_instructions(
    account_id: str, payload: AccountInstructionsIn, db: DbDep
) -> AccountInstructionsOut:
    account_service.get_account(db, account_id)
    row = db.get(AccountInstructions, account_id)
    if row is None:
        row = AccountInstructions(account_id=account_id)
        db.add(row)

    for field, value in payload.model_dump().items():
        setattr(row, field, (value.strip() or None) if isinstance(value, str) else value)

    db.commit()
    logger.info("ai.account_instructions_saved", extra={"context": {"account_id": account_id}})
    return get_account_instructions(account_id, db)


# --------------------------------------------------------------------------- #
# channel
# --------------------------------------------------------------------------- #
@router.get("/channels/{channel_id}/instructions", response_model=ChannelInstructionsOut, dependencies=[ApiKeyDep])
def get_channel_instructions(channel_id: str, db: DbDep) -> ChannelInstructionsOut:
    channel = _require_channel(db, channel_id)
    row = db.get(ChannelInstructions, channel_id)
    account = db.get(Account, channel.account_id)
    return ChannelInstructionsOut(
        channel_id=channel.id,
        account_id=channel.account_id,
        account_name=account.name if account else channel.account_id,
        platform=channel.platform,
        platform_label=platform_label(channel.platform),
        display_name=channel.display_name,
        instructions=row.instructions if row else None,
        configured=bool(row and (row.instructions or "").strip()),
        updated_at=row.updated_at if row else None,
    )


@router.put("/channels/{channel_id}/instructions", response_model=ChannelInstructionsOut, dependencies=[ApiKeyDep])
def put_channel_instructions(
    channel_id: str, payload: ChannelInstructionsIn, db: DbDep
) -> ChannelInstructionsOut:
    channel = _require_channel(db, channel_id)
    row = db.get(ChannelInstructions, channel_id)
    if row is None:
        row = ChannelInstructions(channel_id=channel_id)
        db.add(row)
    row.instructions = (payload.instructions or "").strip() or None
    db.commit()
    logger.info(
        "ai.channel_instructions_saved",
        extra={"context": {"channel_id": channel_id, "account_id": channel.account_id}},
    )
    return get_channel_instructions(channel_id, db)


@router.delete("/channels/{channel_id}/instructions", dependencies=[ApiKeyDep])
def delete_channel_instructions(channel_id: str, db: DbDep) -> dict:
    """Reset to inherited instructions."""
    _require_channel(db, channel_id)
    row = db.get(ChannelInstructions, channel_id)
    if row is not None:
        db.delete(row)
        db.commit()
    return {"channel_id": channel_id, "reset": True}


@router.get(
    "/channels/{channel_id}/effective-instructions",
    response_model=EffectiveInstructionsOut,
    dependencies=[ApiKeyDep],
)
def get_effective_instructions(channel_id: str, db: DbDep) -> EffectiveInstructionsOut:
    """Exactly what would be sent for this channel, with inheritance shown."""
    channel = _require_channel(db, channel_id)
    account = account_service.get_account(db, channel.account_id)

    from app.models.ai_entities import Campaign

    effective = effective_for_channel(
        db.get(AccountInstructions, account.id),
        db.get(ChannelInstructions, channel.id),
        db.get(GlobalInstructions, 1),
    )

    # Render with a throwaway campaign so the preview matches real generation.
    from app.models.ai_entities import Campaign as _C

    preview_campaign = _C(
        id="preview",
        name="preview",
        topic="(campaign topic appears here)",
        language="en",
        status="draft",
    )

    from app.services.platform_limits import limit_hint

    text = build_system_prompt(
        effective,
        account,
        channel,
        platform_label(channel.platform),
        preview_campaign,
        limit_hint(platform_label(channel.platform)),
    )

    return EffectiveInstructionsOut(
        channel_id=channel.id,
        account_id=account.id,
        platform=channel.platform,
        platform_label=platform_label(channel.platform),
        layers=_layers(effective),
        text=text,
        system_rules_preview=SYSTEM_RULES[:400],
    )


def _layers(effective) -> list[InstructionLayerOut]:
    """Precedence order, weakest first. The UI renders this list verbatim."""
    sources = effective.sources
    return [
        InstructionLayerOut(
            key="system",
            label="Safety and quality rules",
            rank=0,
            configured=True,
            detail="Always applied. Cannot be overridden by any instruction.",
        ),
        InstructionLayerOut(
            key="global",
            label="Global instructions",
            rank=1,
            configured=bool(sources.get("global")),
            detail="Applies to every brand.",
        ),
        InstructionLayerOut(
            key="brand",
            label="Brand profile",
            rank=2,
            configured=bool(effective.brand_fields),
            detail="What this brand does, who it is for, and its voice.",
        ),
        InstructionLayerOut(
            key="account",
            label="Brand writing instructions",
            rank=3,
            configured=bool(sources.get("account")),
            detail="Free-text instructions for this brand only.",
        ),
        InstructionLayerOut(
            key="channel",
            label="Channel instructions",
            rank=4,
            configured=bool(sources.get("channel")),
            detail="Overrides for this platform only. The most specific layer.",
        ),
        InstructionLayerOut(
            key="campaign",
            label="Campaign",
            rank=5,
            configured=True,
            detail="Set per campaign. Overrides everything except safety rules.",
        ),
    ]


@router.post("/channels/{channel_id}/test-generation", response_model=TestGenerationOut, dependencies=[ApiKeyDep])
def test_generation(channel_id: str, payload: TestGenerationIn, db: DbDep, settings: SettingsDep) -> TestGenerationOut:
    """Generate a single sample post for this channel. Publishes nothing."""
    channel = _require_channel(db, channel_id)
    account = account_service.get_account(db, channel.account_id)

    from app.models.ai_entities import Campaign as _C

    campaign = _C(
        id="test",
        name="Test generation",
        topic=payload.topic.strip(),
        language="en",
        status="draft",
    )

    providers = ProviderService(db, settings)
    try:
        adapter = providers.adapter_for_campaign(payload.provider_id)
    except AppError as exc:
        return TestGenerationOut(ok=False, error_code=exc.code, message=exc.user_message)

    effective = effective_for_channel(
        db.get(AccountInstructions, account.id),
        db.get(ChannelInstructions, channel.id),
        db.get(GlobalInstructions, 1),
    )

    from app.services.platform_limits import limit_hint

    system_prompt = build_system_prompt(
        effective, account, channel, platform_label(channel.platform), campaign,
        limit_hint(platform_label(channel.platform)),
    )

    try:
        generated = adapter.generate_structured(system_prompt, build_user_prompt(campaign))
    except ProviderError as exc:
        logger.warning(
            "ai.test_generation_failed",
            extra={"context": {"channel_id": channel_id, "error_code": exc.code}},
        )
        return TestGenerationOut(ok=False, error_code=exc.code, message=exc.message)

    return TestGenerationOut(
        ok=True,
        post_text=generated.post_text,
        hashtags=generated.hashtags,
        model=generated.model,
    )


def _require_channel(db: Session, channel_id: str) -> Channel:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.CHANNEL_NOT_FOUND, 404, field="channel_id")
    return channel