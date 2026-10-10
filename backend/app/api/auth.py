"""Operator login: establish and end a browser session.

These routes are the only ones in the application that are deliberately
unauthenticated. `/auth/login` must be reachable by a browser that is not yet
signed in; `/auth/session` must be reachable so the frontend can decide whether
to show the app or the login screen without guessing.

Everything else is protected by `ApiKeyDep`, which accepts either a valid
session cookie or the static API key (see `app.api.deps`).

The session token is delivered only as an HttpOnly cookie. It is never
returned in a response body, so it cannot be read by injected script, and it is
never written to a log line.
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.database.session import get_db
from app.services import auth_service

logger = get_logger("app.auth.api")

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class SessionUserOut(BaseModel):
    """Who is signed in. Contains nothing secret - never a hash, never a token."""

    username: str
    display_name: str | None = None
    authenticated: bool


class LoginStatusOut(BaseModel):
    """Whether login is available at all, and whether a session is active.

    Lets the login screen explain "no operator is configured" instead of
    showing a password box that can never succeed.
    """

    enabled: bool
    authenticated: bool
    username: str | None = None


def _error(status_code: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    """One failure shape for every auth refusal.

    Login never distinguishes "no such user" from "wrong password" in the
    response: both are INVALID_CREDENTIALS with the same text, so the endpoint
    cannot be used to enumerate usernames.
    """
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}},
        headers=headers,
    )


def _client_ip(request: Request) -> str:
    """Best-effort client identity for throttling.

    The left-most X-Forwarded-For entry is the original client when a trusted
    proxy (Render) sets it. A client can spoof this header, which only weakens
    the per-IP window; it never weakens Argon2id or the account lockout.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=auth_service.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.auth_session_ttl_hours * 3600,
        httponly=True,
        # "lax" is correct when the frontend and API share a registrable domain
        # (two *.onrender.com hosts do). "none" is required only when they do
        # not, and browsers demand Secure for it.
        samesite=settings.auth_cookie_samesite,
        secure=settings.auth_cookie_secure_effective,
        path="/",
    )


@router.get("/status", response_model=LoginStatusOut)
def auth_status(db: DbSession, settings: SettingsDep, request: Request) -> LoginStatusOut:
    """Public: is login possible, and is this browser already signed in?"""
    operator = auth_service.get_operator(db)
    enabled = operator is not None and bool(settings.auth_enabled)
    current = auth_service.resolve_session(db, request.cookies.get(auth_service.SESSION_COOKIE_NAME))
    return LoginStatusOut(
        enabled=enabled,
        authenticated=current is not None,
        username=current.username if current else None,
    )


@router.post("/login")
def login(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    """Exchange a password for a session cookie."""
    ip = _client_ip(request)

    blocked_for = auth_service.ip_blocked_for(ip)
    if blocked_for > 0:
        logger.warning("auth.login_rate_limited")
        return _error(
            429,
            "TOO_MANY_ATTEMPTS",
            "Too many attempts. Try again shortly.",
            headers={"Retry-After": str(int(blocked_for) + 1)},
        )

    operator = auth_service.get_operator(db) or auth_service.get_operator_by_username(db, username)

    if operator is None:
        # No such operator: still run a real Argon2id verification so the
        # response time does not reveal whether the account exists.
        auth_service.verify_password(password, auth_service.DUMMY_HASH)
        auth_service.register_failure(ip)
        logger.warning("auth.login_failed", extra={"context": {"reason": "no_operator"}})
        return _error(401, "INVALID_CREDENTIALS", "Incorrect username or password.")

    if auth_service.is_locked(operator):
        logger.warning("auth.login_failed", extra={"context": {"reason": "locked"}})
        return _error(429, "ACCOUNT_LOCKED", "Too many failed attempts. Try again later.")

    if not auth_service.verify_password(password, operator.password_hash):
        auth_service.register_failure(ip)
        auth_service.register_operator_failure(db, operator)
        db.commit()
        logger.warning("auth.login_failed", extra={"context": {"reason": "bad_password"}})
        return _error(401, "INVALID_CREDENTIALS", "Incorrect username or password.")

    if not operator.active:
        logger.warning("auth.login_failed", extra={"context": {"reason": "inactive"}})
        return _error(403, "ACCOUNT_DISABLED", "This account is disabled.")

    token, _row = auth_service.create_session(
        db,
        operator,
        ttl_hours=settings.auth_session_ttl_hours,
        user_agent=request.headers.get("user-agent"),
        ip_address=ip,
    )
    db.commit()
    auth_service.register_success(ip)
    logger.info("auth.login_ok", extra={"context": {"username": operator.username}})

    response = JSONResponse(
        content={
            "success": True,
            "username": operator.username,
            "display_name": operator.display_name,
            "authenticated": True,
        }
    )
    _set_session_cookie(response, settings, token)
    return response


@router.post("/logout")
def logout(request: Request, db: DbSession, settings: SettingsDep) -> Response:
    """End the current session and clear the cookie."""
    auth_service.revoke_session(db, request.cookies.get(auth_service.SESSION_COOKIE_NAME))
    db.commit()
    response = JSONResponse(content={"success": True})
    response.delete_cookie(
        key=auth_service.SESSION_COOKIE_NAME,
        path="/",
        samesite=settings.auth_cookie_samesite,
        secure=settings.auth_cookie_secure_effective,
    )
    logger.info("auth.logout")
    return response


@router.get("/session", response_model=SessionUserOut)
def current_session(request: Request, db: DbSession) -> SessionUserOut:
    """Public: who is this browser signed in as, if anyone?"""
    operator = auth_service.resolve_session(db, request.cookies.get(auth_service.SESSION_COOKIE_NAME))
    if operator is None:
        return SessionUserOut(username="", display_name=None, authenticated=False)
    return SessionUserOut(
        username=operator.username,
        display_name=operator.display_name,
        authenticated=True,
    )


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/change-password")
def change_password(
    request: Request,
    db: DbSession,
    payload: PasswordChangeIn,
) -> Response:
    """Change the operator password from the UI.

    Refuses a password weaker than the deployment policy. Rotating the password
    also invalidates every other session, so an attacker holding a stolen token
    loses it at the same moment the operator gains a valid one.
    """
    operator = auth_service.resolve_session(db, request.cookies.get(auth_service.SESSION_COOKIE_NAME))
    if operator is None:
        return _error(401, "UNAUTHORIZED", "Sign in first.")

    if not auth_service.verify_password(payload.current_password, operator.password_hash):
        logger.warning("auth.password_change_failed")
        return _error(401, "INVALID_CREDENTIALS", "Current password is incorrect.")

    problem = auth_service.password_problem(payload.new_password)
    if problem:
        return _error(422, "WEAK_PASSWORD", problem)

    operator.password_hash = auth_service.hash_password(payload.new_password)
    operator.session_epoch = int(operator.session_epoch or 1) + 1
    db.flush()
    auth_service.revoke_all_sessions(db, operator.id)
    db.commit()

    logger.info("auth.password_changed")
    return JSONResponse(
        content={
            "success": True,
            "message": "Password updated. Every other session has been signed out.",
        }
    )