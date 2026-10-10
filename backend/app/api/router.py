"""API router aggregation."""
from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    accounts,
    ai_campaigns,
    ai_instructions,
    ai_providers,
    channels,
    integrations,
    media,
    publishing,
    system,
)

api_router = APIRouter()
api_router.include_router(accounts.router)
api_router.include_router(channels.router)
api_router.include_router(publishing.router)
api_router.include_router(media.router)
api_router.include_router(ai_providers.router)
api_router.include_router(ai_instructions.router)
api_router.include_router(ai_campaigns.router)
api_router.include_router(integrations.router)
api_router.include_router(system.router)


@api_router.get("/health", tags=["system"])
def health() -> dict:
    """Liveness plus configuration visibility (never secrets)."""
    from app.core.config import get_settings
    from app.database.session import session_scope
    from app.models.entities import Account

    settings = get_settings()
    try:
        with session_scope() as db:
            seeded = db.query(Account).count() > 0
        database = "ok"
    except Exception as exc:  # pragma: no cover
        seeded = False
        database = f"error:{type(exc).__name__}"

    return {
        "status": "ok" if database == "ok" else "degraded",
        "app": settings.app_name,
        "environment": settings.app_env,
        "n8n_configured": bool(settings.n8n_publish_webhook_url),
        "database": database,
        "accounts_seeded": seeded,
    }