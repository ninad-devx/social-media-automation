"""Shared FastAPI dependencies."""
from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.database.session import get_db
from app.services.n8n_service import N8nService


def get_n8n(settings: Annotated[Settings, Depends(get_settings)]) -> N8nService:
    return N8nService(settings)


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Optional API-key gate.

    Disabled while `API_KEY` is unset, which keeps local development friction
    free. Setting API_KEY in the environment turns header auth on for every
    /api route in one place, so auth can be added later without restructuring.

    The comparison is constant-time so a caller cannot recover the key by
    measuring how long a rejection takes.
    """
    expected = settings.api_key
    if not expected:
        return
    # compare_digest requires bytes; str inputs with non-ASCII characters raise
    # TypeError, which would surface as a 500 and tell the caller it guessed a
    # malformed request rather than a wrong key.
    supplied = (x_api_key or "").encode("utf-8")
    if not hmac.compare_digest(supplied, expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Invalid or missing API key."}},
        )


DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
N8nDep = Annotated[N8nService, Depends(get_n8n)]
ApiKeyDep = Depends(require_api_key)