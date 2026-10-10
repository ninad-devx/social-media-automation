"""AI provider configuration endpoints.

GET responses never contain secret material, only a mask.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Response, status

from app.api.deps import ApiKeyDep, DbDep, SettingsDep
from app.schemas.ai import (
    DetectProviderIn,
    DetectProviderOut,
    ModelListOut,
    ProviderCatalogueItem,
    ProviderCreate,
    ProviderOut,
    ProviderTestResult,
    ProviderUpdate,
)
from app.services.ai.detect import detect_slug
from app.services.ai.registry import get_spec
from app.services.ai_provider_service import ProviderService, catalogue, public_view

router = APIRouter(prefix="/ai/providers", tags=["ai-providers"])


@router.post("/detect", response_model=DetectProviderOut, dependencies=[ApiKeyDep])
def detect_provider(payload: DetectProviderIn, settings: SettingsDep) -> DetectProviderOut:
    """Identify a pasted key and list its models, without saving anything.

    This is what lets a user paste one key and immediately see the right
    provider and a populated model list. The key is used for the probe and
    then dropped: it is never written to the database, logged, or returned.
    """
    from app.services.ai.registry import build_adapter

    if payload.provider_slug:
        # The caller already chose; trust them over the heuristic.
        guess = detect_slug(payload.api_key, payload.base_url)
        slug = payload.provider_slug.strip().lower()
        reason = f"You chose {get_spec(slug).display_name}."
        confident = True
    else:
        guess = detect_slug(payload.api_key, payload.base_url)
        slug = guess.slug
        reason = guess.reason
        confident = guess.confident

    spec = get_spec(slug)

    base = DetectProviderOut(
        detected_slug=spec.slug,
        confident=confident,
        reason=reason,
        display_name=spec.display_name,
        default_base_url=spec.default_base_url or "",
        default_model=spec.default_model,
        supports_model_discovery=spec.supports_model_discovery,
        requires_base_url=spec.requires_base_url,
        key_label=spec.key_label,
        help_url=spec.help_url,
        credentials_ok=False,
    )

    # A custom endpoint with no base URL cannot be probed; say so rather than
    # failing with an obscure network error.
    if spec.requires_base_url and not payload.base_url:
        base.reason = (
            "This looks like a custom or OpenAI-compatible endpoint. "
            "Add its base URL to load models."
        )
        return base

    try:
        adapter = build_adapter(
            spec.slug,
            api_key=payload.api_key,
            base_url=payload.base_url or spec.default_base_url,
            model=spec.default_model,
            timeout=min(settings.ai_timeout_seconds, 20.0),
        )
    except Exception:
        base.error_code = "AI_NOT_CONFIGURED"
        base.detail = "Could not prepare the provider. Check the base URL."
        return base

    info = adapter.validate_credentials()
    base.credentials_ok = info.ok
    base.detail = info.detail
    base.error_code = info.error_code

    # Validate and list in one round trip when we can, so the user gets
    # models and a validity answer together.
    if info.ok and spec.supports_model_discovery:
        models = adapter.list_models()
        if models.ok:
            base.models = models.models

    return base


def _service(db: DbDep, settings: SettingsDep) -> ProviderService:
    return ProviderService(db, settings)


def _out(provider, service: ProviderService) -> ProviderOut:
    return ProviderOut(
        **public_view(
            provider,
            encryption_ready=service.encryption_available(),
            masked_key=service.mask_for(provider),
        )
    )


@router.get("/catalogue", response_model=list[ProviderCatalogueItem], dependencies=[ApiKeyDep])
def provider_catalogue() -> list[ProviderCatalogueItem]:
    """Supported provider types and their defaults."""
    return [ProviderCatalogueItem(**item) for item in catalogue()]


@router.get("", response_model=list[ProviderOut], dependencies=[ApiKeyDep])
def list_providers(db: DbDep, settings: SettingsDep) -> list[ProviderOut]:
    service = _service(db, settings)
    return [_out(p, service) for p in service.list_providers()]


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED, dependencies=[ApiKeyDep])
def create_provider(payload: ProviderCreate, db: DbDep, settings: SettingsDep) -> ProviderOut:
    service = _service(db, settings)
    provider = service.create(
        provider_slug=payload.provider_slug,
        display_name=payload.display_name or "",
        api_key=payload.api_key,
        base_url=payload.base_url,
        model=payload.model,
        organization=payload.organization,
        project=payload.project,
        enabled=payload.enabled,
        make_default=payload.make_default,
    )
    return _out(provider, service)


@router.patch("/{provider_id}", response_model=ProviderOut, dependencies=[ApiKeyDep])
def update_provider(provider_id: str, payload: ProviderUpdate, db: DbDep, settings: SettingsDep) -> ProviderOut:
    service = _service(db, settings)
    provider = service.update(provider_id, payload.model_dump(exclude_unset=True))
    return _out(provider, service)


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[ApiKeyDep],
)
def delete_provider(provider_id: str, db: DbDep, settings: SettingsDep):
    _service(db, settings).delete(provider_id)
    return Response(status_code=204)


@router.post("/{provider_id}/test", response_model=ProviderTestResult, dependencies=[ApiKeyDep])
def test_provider(provider_id: str, db: DbDep, settings: SettingsDep) -> ProviderTestResult:
    """Validate credentials. Generates no content and publishes nothing."""
    service = _service(db, settings)
    provider = service.get(provider_id)
    info = service.test_connection(provider)
    return ProviderTestResult(
        ok=info.ok,
        detail=info.detail,
        error_code=info.error_code,
        tested_at=provider.last_tested_at,
    )


@router.get("/{provider_id}/models", response_model=ModelListOut, dependencies=[ApiKeyDep])
def list_provider_models(provider_id: str, db: DbDep, settings: SettingsDep) -> ModelListOut:
    """Model discovery where the provider supports it."""
    service = _service(db, settings)
    provider = service.get(provider_id)
    info = service.list_models(provider)
    return ModelListOut(
        ok=info.ok,
        models=info.models,
        detail=info.detail,
        error_code=info.error_code,
        # Always allow typing a model by hand.
        manual_entry_allowed=True,
    )