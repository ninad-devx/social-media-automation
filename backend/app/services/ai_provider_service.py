"""AI provider configuration service.

Owns encryption, validation and adapter construction. The API layer never
touches ciphertext or decrypted keys directly.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.ai_entities import AIProvider
from app.services.ai.base import ProviderError, ProviderInfo
from app.services.ai.registry import build_adapter, get_spec, list_specs
from app.services.credentials import EncryptionUnavailable, build_cipher, mask_secret
from app.services.errors import AppError, ErrorCode

logger = get_logger("app.ai.providers")


def new_id() -> str:
    return f"aip_{secrets.token_hex(8)}"


class ProviderService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._cipher = build_cipher(settings)

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def list_providers(self) -> list[AIProvider]:
        return list(
            self._db.scalars(
                select(AIProvider).order_by(AIProvider.is_default.desc(), AIProvider.created_at)
            ).all()
        )

    def get(self, provider_id: str) -> AIProvider:
        provider = self._db.get(AIProvider, provider_id)
        if provider is None:
            raise AppError(ErrorCode.NOT_FOUND, 404, field="provider_id")
        return provider

    def get_default(self) -> AIProvider | None:
        return self._db.scalars(
            select(AIProvider).where(AIProvider.enabled.is_(True)).order_by(AIProvider.is_default.desc())
        ).first()

    def encryption_available(self) -> bool:
        return self._cipher.available

    def mask_for(self, provider: AIProvider) -> str | None:
        """Decrypt only long enough to build a display mask.

        If the encryption key is missing or rotated, we return None rather than
        failing the whole list request.
        """
        try:
            return mask_secret(self._cipher.decrypt(provider.api_key_encrypted))
        except EncryptionUnavailable:
            return None

    def decrypt(self, provider: AIProvider) -> str:
        try:
            return self._cipher.decrypt(provider.api_key_encrypted)
        except EncryptionUnavailable as exc:
            raise AppError(
                ErrorCode.AI_NOT_CONFIGURED,
                500,
                log_context={"reason": str(exc)},
            ) from None

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def create(
        self,
        *,
        provider_slug: str,
        display_name: str,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        enabled: bool = True,
        make_default: bool = False,
    ) -> AIProvider:
        spec = get_spec(provider_slug)

        if not api_key or not api_key.strip():
            raise AppError(ErrorCode.AI_PROVIDER_KEY_REQUIRED, 400, field="api_key")

        if spec.requires_base_url and not (base_url or "").strip():
            raise AppError(
                ErrorCode.AI_PROVIDER_BASE_URL_REQUIRED,
                400,
                field="base_url",
            )

        try:
            encrypted = self._cipher.encrypt(api_key.strip())
        except EncryptionUnavailable as exc:
            # Never silently store a key in plain text.
            raise AppError(ErrorCode.AI_ENCRYPTION_NOT_CONFIGURED, 500, log_context={"reason": str(exc)}) from None

        provider = AIProvider(
            id=new_id(),
            provider_slug=spec.slug,
            display_name=(display_name or spec.display_name).strip()[:120],
            api_key_encrypted=encrypted,
            base_url=(base_url or spec.default_base_url),
            model=(model or spec.default_model or "").strip(),
            organization=organization,
            project=project,
            enabled=enabled,
            is_default=False,
        )
        self._db.add(provider)
        self._db.flush()

        if make_default or not self.list_providers_with_default():
            self._set_default(provider.id)

        self._db.commit()
        logger.info(
            "ai.provider_created",
            extra={"context": {"provider_id": provider.id, "slug": spec.slug, "model": provider.model}},
        )
        return provider

    def list_providers_with_default(self) -> bool:
        """True when a default provider already exists."""
        return (
            self._db.scalar(select(AIProvider.id).where(AIProvider.is_default.is_(True)).limit(1)) is not None
        )

    def update(self, provider_id: str, changes: dict) -> AIProvider:
        provider = self.get(provider_id)
        spec = get_spec(provider.provider_slug)

        if "api_key" in changes and changes["api_key"]:
            try:
                provider.api_key_encrypted = self._cipher.encrypt(str(changes["api_key"]).strip())
            except EncryptionUnavailable as exc:
                raise AppError(ErrorCode.AI_ENCRYPTION_NOT_CONFIGURED, 500, log_context={"reason": str(exc)}) from None

        for field in ("display_name", "base_url", "model", "organization", "project"):
            if field in changes and changes[field] is not None:
                setattr(provider, field, str(changes[field]).strip()[:512])

        if "enabled" in changes and changes["enabled"] is not None:
            provider.enabled = bool(changes["enabled"])

        if spec.requires_base_url and not (provider.base_url or "").strip():
            raise AppError(ErrorCode.AI_PROVIDER_BASE_URL_REQUIRED, 400, field="base_url")

        if "make_default" in changes and changes["make_default"]:
            self._set_default(provider.id)

        self._db.commit()
        logger.info(
            "ai.provider_updated",
            extra={"context": {"provider_id": provider.id, "fields": sorted(changes.keys())}},
        )
        return provider

    def _set_default(self, provider_id: str) -> None:
        self._db.execute(update(AIProvider).values(is_default=False))
        provider = self._db.get(AIProvider, provider_id)
        if provider is not None:
            provider.is_default = True
            self._db.flush()

    def delete(self, provider_id: str) -> None:
        provider = self.get(provider_id)
        self._db.delete(provider)
        self._db.flush()
        # Promote another enabled provider so the app is never left without one.
        remaining = list(self._db.scalars(select(AIProvider).where(AIProvider.enabled.is_(True))).all())
        if remaining and not any(p.is_default for p in remaining):
            remaining[0].is_default = True
        self._db.commit()
        logger.info("ai.provider_deleted", extra={"context": {"provider_id": provider_id}})

    # ------------------------------------------------------------------ #
    # adapter construction
    # ------------------------------------------------------------------ #
    def adapter_for(self, provider: AIProvider):
        return build_adapter(
            provider.provider_slug,
            api_key=self.decrypt(provider),
            base_url=provider.base_url,
            model=provider.model,
            timeout=self._settings.ai_timeout_seconds,
            organization=provider.organization,
            project=provider.project,
        )

    def adapter_for_campaign(self, provider_id: str | None = None):
        """Explicit provider, else the default. Never falls back silently to a
        different paid provider."""
        provider = self.get(provider_id) if provider_id else self.get_default()
        if provider is None:
            raise AppError(
                ErrorCode.AI_NO_PROVIDER,
                400,
                log_context={"hint": "configure a provider in Settings"},
            )
        if not provider.enabled:
            raise AppError(
                ErrorCode.AI_PROVIDER_DISABLED,
                400,
                field="provider_id",
            )
        return self.adapter_for(provider)

    # ------------------------------------------------------------------ #
    # probes
    # ------------------------------------------------------------------ #
    def test_connection(self, provider: AIProvider) -> ProviderInfo:
        """Validate credentials without generating content."""
        try:
            adapter = self.adapter_for(provider)
        except AppError as exc:
            return ProviderInfo(ok=False, detail=exc.user_message, error_code=exc.code)

        try:
            info = adapter.validate_credentials()
        except ProviderError as exc:  # defensive: adapters already catch
            info = ProviderInfo(ok=False, detail=exc.message, error_code=exc.code)

        provider.last_status = "ok" if info.ok else "error"
        provider.last_error_code = info.error_code
        provider.last_tested_at = datetime.now(timezone.utc)
        self._db.commit()

        # Logged without the key, ever.
        logger.info(
            "ai.provider_tested",
            extra={
                "context": {
                    "provider_id": provider.id,
                    "slug": provider.provider_slug,
                    "ok": info.ok,
                    "error_code": info.error_code,
                }
            },
        )
        return info

    def list_models(self, provider: AIProvider) -> ProviderInfo:
        try:
            adapter = self.adapter_for(provider)
        except AppError as exc:
            return ProviderInfo(ok=False, detail=exc.user_message, error_code=exc.code)
        return adapter.list_models()


def public_view(provider: AIProvider, *, encryption_ready: bool, masked_key: str | None = None) -> dict:
    """The ONLY shape the API returns. Contains no secret material.

    `masked_key` must be computed by the service (which can decrypt); this
    function never touches ciphertext.
    """
    spec = get_spec(provider.provider_slug)

    return {
        "id": provider.id,
        "provider_slug": provider.provider_slug,
        "provider_name": spec.display_name,
        "display_name": provider.display_name,
        "base_url": provider.base_url,
        "model": provider.model,
        "organization": provider.organization,
        "project": provider.project,
        "enabled": provider.enabled,
        "is_default": provider.is_default,
        "supports_model_discovery": spec.supports_model_discovery,
        "requires_base_url": spec.requires_base_url,
        "default_base_url": spec.default_base_url,
        "default_model": spec.default_model,
        "key_label": spec.key_label,
        "help_url": spec.help_url,
        "has_api_key": bool(provider.api_key_encrypted),
        "api_key_masked": masked_key,
        "last_status": provider.last_status,
        "last_error_code": provider.last_error_code,
        "last_tested_at": provider.last_tested_at,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
        "encryption_available": encryption_ready,
    }


def catalogue() -> list[dict]:
    """Static description of supported providers, for the Settings UI."""
    return [
        {
            "slug": s.slug,
            "display_name": s.display_name,
            "supports_model_discovery": s.supports_model_discovery,
            "requires_base_url": s.requires_base_url,
            "default_base_url": s.default_base_url,
            "default_model": s.default_model,
            "key_label": s.key_label,
            "help_url": s.help_url,
            "supports_organization": s.supports_organization,
            "supports_project": s.supports_project,
        }
        for s in list_specs()
    ]