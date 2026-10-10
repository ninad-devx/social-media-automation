"""Shared FastAPI dependencies."""
from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.database.session import get_db
from app.services.n8n_service import N8nService


def get_n8n(settings: Annotated[Settings, Depends(get_settings)]) -> N8nService:
    return N8nService(settings)


def require_api_key(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Gate every /api route.

    Two credentials are accepted, so adding a login did not break anything
    that already worked:

    1. **A signed-in browser session** - an HttpOnly cookie validated against
       the `operator_sessions` table. This is what the React app uses.
    2. **The static `API_KEY`** via `X-API-Key` - what service-to-service
       callers use, and what keeps working for anyone who never signs in.

    Behaviour is unchanged when no operator exists: with no row there is no
    session to resolve, so the gate falls back to exactly the old API-key
    behaviour, including staying open when API_KEY is empty - which is what
    local development and the existing test suite rely on.

    A browser that cannot satisfy either credential gets 401, which the
    frontend uses to decide whether to show the login screen.
    """
    expected = settings.api_key

    # 1. Session cookie. Cheap to attempt and the common case in a browser.
    if settings.auth_enabled:
        from app.services import auth_service

        token = request.cookies.get(auth_service.SESSION_COOKIE_NAME)
        if token:
            if auth_service.resolve_session(db, token) is not None:
                return
            # A cookie was presented but is expired or revoked. Fall through
            # so a valid X-API-Key still works, otherwise a stale cookie would
            # lock out an otherwise-authorised machine client.

    # 2. Static API key, for non-browser callers.
    if expected:
        # compare_digest requires bytes; str inputs with non-ASCII characters
        # raise TypeError, which would surface as a 500 and tell the caller it
        # guessed a malformed request rather than a wrong key.
        supplied = (x_api_key or "").encode("utf-8")
        if hmac.compare_digest(supplied, expected.encode("utf-8")):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Invalid or missing API key."}},
        )

    # 3. No API_KEY configured: local development or the test suite, where
    #    this gate has always been open. Preserve that exactly.
    return


DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
N8nDep = Annotated[N8nService, Depends(get_n8n)]
ApiKeyDep = Depends(require_api_key)