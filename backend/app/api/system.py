"""System / integration status overview.

One endpoint that answers "is anything actually broken right now?". Every value
comes from a real check or a stored operation result - never from "a text field
is non-empty".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ApiKeyDep, SettingsDep
from app.core.config import Settings
from app.core.logging import get_logger
from app.database.session import get_db
from app.models.ai_entities import AIProvider
from app.models.entities import Account, Channel, Integration, PublishLog
from app.schemas.integration import SystemStatusOut
from app.services import account_service
from app.services.credentials import build_cipher
from app.services.integration_service import (
    STATUS_CONNECTED,
    STATUS_DISABLED,
    STATUS_REAUTH_REQUIRED,
    IntegrationService,
)
from app.services.n8n_service import N8nService

logger = get_logger("app.system")

router = APIRouter(tags=["system"])

DbSession = Annotated[Session, Depends(get_db)]


def _db_status(db: Session) -> dict:
    try:
        db.execute(select(func.count()).select_from(Account))
        return {"ok": True}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": type(exc).__name__}


def _media_status(settings: Settings) -> dict:
    """Whether Buffer can download a freshly uploaded file.

    Buffer fetches every attachment itself from a public URL, so what matters
    is only whether that URL is reachable from the internet. Object storage is
    always reachable; local disk is reachable when PUBLIC_MEDIA_BASE_URL points
    at a public address, because /api/media/public is signed and unauthenticated.

    The warning below used to be driven by `media_storage_backend == "s3"`
    alone, which contradicted the `publishable` field reported right beside it
    and told operators to configure S3 on deployments where signed public URLs
    already worked. Both now read from this one function.
    """
    public_base = settings.public_media_base_url.strip()
    is_local_url = public_base.startswith(("http://localhost", "http://127.0.0.1"))
    public_url_ok = bool(public_base) and not is_local_url

    return {
        "backend": settings.media_storage_backend,
        "public_base_url_configured": bool(public_base),
        "publishable": settings.media_storage_backend == "s3" or public_url_ok,
    }


@router.get("/system/status", response_model=SystemStatusOut, dependencies=[ApiKeyDep])
def system_status(
    db: DbSession,
    settings: SettingsDep,
    n8n_probe: Annotated[bool, Query(description="Probe the n8n webhook (network call)")] = False,
) -> SystemStatusOut:
    """Aggregated health for the Settings dashboard."""
    service = IntegrationService(db, settings)
    integrations = service.list_integrations()
    media_state = _media_status(settings)

    # --- n8n ---------------------------------------------------------------
    n8n = N8nService(settings)
    n8n_state: dict[str, Any] = {
        "configured": n8n.is_configured(),
        "webhook_url": settings.n8n_publish_webhook_url,
        "reachable": None,
        "in_use": not settings.uses_direct_publishing,
    }
    if n8n_probe and n8n.is_configured():
        n8n_state.update(n8n.health())

    # --- encryption --------------------------------------------------------
    cipher = build_cipher(settings)
    encryption = {
        "configured": cipher.available,
        "using_legacy_ai_key": bool(
            settings.ai_encryption_key and not settings.credential_encryption_key
        ),
        "key_source": (
            "CREDENTIAL_ENCRYPTION_KEY"
            if settings.credential_encryption_key
            else "AI_ENCRYPTION_KEY"
            if settings.ai_encryption_key
            else None
        ),
    }

    # --- integrations ------------------------------------------------------
    by_status: dict[str, int] = {}
    for integration in integrations:
        by_status[integration.status] = by_status.get(integration.status, 0) + 1

    channels = db.scalars(select(Channel)).all()
    active_channels = [c for c in channels if c.active and c.archived_at is None]
    live_channels = [c for c in channels if c.archived_at is None]
    disconnected = [c for c in active_channels if c.connection_status == "reauth_required"]
    locked = [c for c in active_channels if c.is_locked]
    queue_paused = [c for c in active_channels if c.queue_paused]
    unbound = [c for c in live_channels if not c.integration_id]

    integrations_state = {
        "total": len(integrations),
        "by_status": by_status,
        "connected": by_status.get(STATUS_CONNECTED, 0),
        "disabled": by_status.get(STATUS_DISABLED, 0),
        "needs_reauth": by_status.get(STATUS_REAUTH_REQUIRED, 0),
        "channels_bound": sum(1 for c in live_channels if c.integration_id),
        # "Unbound" counts live rows only. Counting archived ones made a
        # correctly removed channel look like outstanding work forever.
        "channels_unbound": len(unbound),
        "channels_disconnected": len(disconnected),
        "channels_locked": len(locked),
        "channels_queue_paused": len(queue_paused),
        "channels_archived": sum(1 for c in channels if c.archived_at is not None),
    }

    # --- AI ----------------------------------------------------------------
    providers = db.scalars(select(AIProvider)).all()
    ai = {
        "providers_total": len(providers),
        "providers_enabled": sum(1 for p in providers if p.enabled),
        "has_default": any(p.is_default and p.enabled for p in providers),
        "ready": any(p.enabled for p in providers) and cipher.available,
    }

    # --- recent failures ---------------------------------------------------
    since = datetime.now(timezone.utc) - timedelta(days=7)
    failures = db.scalars(
        select(PublishLog)
        .where(PublishLog.status == "failed", PublishLog.created_at >= since)
        .order_by(PublishLog.created_at.desc())
        .limit(10)
    ).all()
    recent_failures = [
        {
            "request_id": f.request_id,
            "account_id": f.account_id,
            "channel_id": f.channel_id,
            "platform": f.platform,
            "error_code": f.error_code,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in failures
    ]

    counts = {
        "accounts": int(db.scalar(select(func.count()).select_from(Account)) or 0),
        "accounts_active": int(
            db.scalar(
                select(func.count()).select_from(Account).where(Account.active.is_(True))
            )
            or 0
        ),
        "channels": len(channels),
        # Split by state so an operator can see at a glance whether "channels"
        # means live ones. The previous single pair counted archived rows as
        # active, which made a correct removal look like a failure to remove.
        "channels_active": len(active_channels),
        "channels_inactive": sum(
            1 for c in channels if not c.active and c.archived_at is None
        ),
        "channels_archived": sum(1 for c in channels if c.archived_at is not None),
        "channels_publishable": sum(
            1 for c in active_channels if account_service.is_publishable(c)
        ),
    }

    return SystemStatusOut(
        environment=settings.app_env,
        publish_engine=settings.publish_engine,
        backend={
            "ok": True,
            "app": settings.app_name,
            "api_prefix": settings.api_prefix,
            "debug": settings.debug,
            # Present so the UI can point the operator at the right file
            # without ever exposing a value.
            "internal_publish_enabled": bool(settings.internal_service_secret),
        },
        database=_db_status(db),
        n8n=n8n_state,
        encryption=encryption,
        media=media_state,
        integrations=integrations_state,
        ai=ai,
        counts=counts,
        recent_failures=recent_failures,
        issues=build_issues(
            settings=settings,
            encryption=encryption,
            integrations_state=integrations_state,
            n8n=n8n_state,
            media_ok=media_state["publishable"],
            failures=len(recent_failures),
        ),
    )


def build_issues(
    *,
    settings,
    encryption: dict,
    integrations_state: dict,
    n8n: dict,
    media_ok: bool,
    failures: int,
) -> list[dict]:
    """Actionable, secret-free troubleshooting entries."""
    issues: list[dict] = []

    if not encryption["configured"]:
        issues.append(
            {
                "severity": "error",
                "area": "security",
                "message": "No encryption key is configured, so integration tokens cannot be stored.",
                "action": "Set CREDENTIAL_ENCRYPTION_KEY in backend/.env and restart.",
            }
        )

    if integrations_state["total"] == 0:
        issues.append(
            {
                "severity": "warning",
                "area": "integrations",
                "message": "No Buffer integration is configured yet.",
                "action": "Add one in Settings > Buffer Integrations.",
            }
        )
    elif integrations_state["needs_reauth"]:
        issues.append(
            {
                "severity": "error",
                "area": "integrations",
                "message": f"{integrations_state['needs_reauth']} integration(s) need re-authorising.",
                "action": "Open the integration, enter a fresh token, and press Test connection.",
            }
        )

    if settings.uses_direct_publishing and integrations_state["connected"] == 0:
        issues.append(
            {
                "severity": "error",
                "area": "publishing",
                "message": "Direct publishing is enabled but no integration is connected.",
                "action": "Connect a Buffer integration, or set PUBLISH_ENGINE=n8n.",
            }
        )

    if n8n["in_use"] and not n8n["configured"]:
        issues.append(
            {
                "severity": "error",
                "area": "n8n",
                "message": "The n8n webhook URL is not configured but the n8n engine is selected.",
                "action": "Set N8N_PUBLISH_WEBHOOK_URL in backend/.env.",
            }
        )

    if n8n["in_use"] and settings.n8n_webhook_secret and settings.internal_service_secret:
        issues.append(
            {
                "severity": "info",
                "area": "n8n",
                "message": "Both the webhook secret and the internal service secret are set.",
                "action": "The generic workflow will authenticate to /api/internal/publish.",
            }
        )

    if not media_ok and settings.media_storage_backend != "s3":
        issues.append(
            {
                "severity": "warning",
                "area": "media",
                "message": "Media is served from this machine, so Buffer cannot download it.",
                "action": "Set MEDIA_STORAGE_BACKEND=s3 (or a stable public media URL) before publishing images.",
            }
        )

    if failures:
        issues.append(
            {
                "severity": "warning",
                "area": "publishing",
                "message": f"{failures} publishing failure(s) in the last 7 days.",
                "action": "Check Publishing History for the exact error.",
            }
        )

    if settings.is_production and not settings.api_key:
        issues.append(
            {
                "severity": "error",
                "area": "security",
                "message": "API_KEY is not set in production, so /api is unauthenticated.",
                "action": "Set API_KEY (and rotate it with your clients).",
            }
        )

    return issues
