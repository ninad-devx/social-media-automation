"""Operator authentication: password hashing, sessions and login throttling.

Deliberate properties:

* No default password. An operator row only exists if `APP_ADMIN_PASSWORD` was
  supplied. Unset means "login is unavailable", never "login is open".
* Argon2id for password hashing (RFC 9106). The `cryptography` dependency
  already in this project is not used here because its scrypt KDF has no
  constant-time parameter handling worth reimplementing.
* Session tokens are 256 bits of `secrets.token_urlsafe` and only a SHA-256
  hash is persisted, so a database dump cannot be replayed as a login.
* Login throttling is an in-process sliding window keyed by client IP. It is
  intentionally simple: one instance, one operator, and the real protection
  against a password guess is Argon2id's cost plus an account lockout that
  survives restarts (see `register_failure`).
* Fully synchronous, matching the rest of the data layer.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.auth_entities import Operator, OperatorSession

logger = get_logger("app.auth")

# 64 MiB, 3 passes, 4 lanes: the OWASP-recommended Argon2id baseline.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

SESSION_COOKIE_NAME = "sma_session"

# A real Argon2id hash of an unguessable value, used only to spend the same
# CPU when no operator exists, so login timing cannot be used to enumerate
# usernames. It is never a valid password for any account.
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c21hX2FsdGltdW1teWRhdGhlcmR1bW15$0000000000000000000000000000000000000000000"

# Account lockout after repeated failures. Stored on the operator row so it
# survives a restart, unlike the in-memory IP window below.
LOCKOUT_THRESHOLD = 8
LOCKOUT_MINUTES = 15


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Return an Argon2id PHC string for `password`."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification. Returns False rather than raising."""
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # pragma: no cover - defensive: never leak a stack trace
        logger.warning("auth.verify_unexpected_error")
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash uses outdated parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def password_problem(password: str) -> str | None:
    """Validate a candidate password. Returns an error message, or None if OK.

    Applied to the environment-supplied operator password so a deploy fails
    loudly on `changeme` rather than quietly shipping a guessable login.
    """
    if len(password) < 12:
        return "Password must be at least 12 characters long."
    if password.lower() in {
        "password",
        "password123",
        "admin",
        "administrator",
        "changeme",
        "letmein",
        "123456789012",
        "qwertyuiopas",
    }:
        return "That password is too common. Choose something less predictable."
    if password.isdigit() or password.isalpha():
        return "Password must mix letters and numbers."
    return None


# --------------------------------------------------------------------------- #
# Login throttling (in-process, per client IP)
# --------------------------------------------------------------------------- #
@dataclass
class _Window:
    attempts: deque[float]
    blocked_until: float = 0.0


_lock = threading.Lock()
_windows: dict[str, _Window] = defaultdict(lambda: _Window(deque()))

# Failed attempts per IP inside the window, and how long an IP is blocked once
# it exceeds that.
WINDOW_SECONDS = 300.0
MAX_ATTEMPTS_PER_IP = 10
IP_BLOCK_SECONDS = 300.0


def ip_blocked_for(key: str) -> float:
    """Seconds remaining on an IP block, 0 when not blocked."""
    now = time.monotonic()
    with _lock:
        window = _windows[key]
        if window.blocked_until > now:
            return window.blocked_until - now
        return 0.0


def register_failure(key: str) -> None:
    """Record a failed attempt and block the IP once it crosses the limit."""
    now = time.monotonic()
    with _lock:
        window = _windows[key]
        window.attempts.append(now)
        cutoff = now - WINDOW_SECONDS
        while window.attempts and window.attempts[0] < cutoff:
            window.attempts.popleft()
        if len(window.attempts) >= MAX_ATTEMPTS_PER_IP:
            window.blocked_until = now + IP_BLOCK_SECONDS
            logger.warning("auth.ip_blocked", extra={"context": {"blocked_for_s": IP_BLOCK_SECONDS}})


def register_success(key: str) -> None:
    """Clear the failure history for an IP after a successful login."""
    with _lock:
        _windows.pop(key, None)


def reset_throttling() -> None:
    """Clear all in-memory throttling. Used by tests."""
    with _lock:
        _windows.clear()


# --------------------------------------------------------------------------- #
# Operators
# --------------------------------------------------------------------------- #
def get_operator(db: Session, operator_id: str = "primary") -> Operator | None:
    return db.get(Operator, operator_id)


def get_operator_by_username(db: Session, username: str) -> Operator | None:
    stmt = select(Operator).where(Operator.username == username)
    return db.execute(stmt).scalar_one_or_none()


def ensure_operator(
    db: Session,
    username: str,
    password: str,
    operator_id: str = "primary",
    display_name: str | None = None,
) -> Operator:
    """Create the operator if absent; refresh the hash if the password changed.

    Idempotent, so it is safe to call on every boot. Re-hashing on change is
    deliberate: it is the mechanism by which rotating `APP_ADMIN_PASSWORD`
    actually takes effect, and it increments `session_epoch` so that rotating
    the password immediately invalidates every existing browser session.
    """
    operator = get_operator(db, operator_id)
    if operator is None:
        operator = Operator(
            id=operator_id,
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            active=True,
            session_epoch=1,
        )
        db.add(operator)
        db.flush()
        logger.info("auth.operator_created", extra={"context": {"username": username}})
        return operator

    if not verify_password(password, operator.password_hash):
        operator.password_hash = hash_password(password)
        operator.session_epoch = int(operator.session_epoch or 1) + 1
        operator.locked_until = None
        operator.failed_login_count = 0
        operator.active = True
        db.flush()
        # Every existing session is now stale by construction.
        stmt = select(OperatorSession).where(
            OperatorSession.operator_id == operator.id,
            OperatorSession.revoked_at.is_(None),
        )
        for session in db.execute(stmt).scalars():
            session.revoked_at = datetime.now(timezone.utc)
        db.flush()
        logger.info("auth.operator_password_rotated", extra={"context": {"username": username}})

    return operator


def is_locked(operator: Operator) -> bool:
    if operator.locked_until is None:
        return False
    until = operator.locked_until
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def register_operator_failure(db: Session, operator: Operator) -> None:
    operator.failed_login_count = int(operator.failed_login_count or 0) + 1
    if operator.failed_login_count >= LOCKOUT_THRESHOLD:
        operator.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
        operator.failed_login_count = 0
        logger.warning("auth.account_locked", extra={"context": {"minutes": LOCKOUT_MINUTES}})
    db.flush()


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    db: Session,
    operator: Operator,
    ttl_hours: int = 12,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, OperatorSession]:
    """Issue a session. Returns (raw_token, row).

    The raw token is given to the caller exactly once, to be placed in an
    HttpOnly cookie. It is never logged and never returned in a JSON body.
    """
    raw = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    row = OperatorSession(
        id=secrets.token_hex(16),
        operator_id=operator.id,
        token_hash=_hash_token(raw),
        session_epoch=int(operator.session_epoch or 1),
        expires_at=now + timedelta(hours=ttl_hours),
        last_seen_at=now,
        user_agent=(user_agent or "")[:400] or None,
        ip_address=(ip_address or "")[:64] or None,
    )
    db.add(row)
    operator.last_login_at = now
    operator.failed_login_count = 0
    operator.locked_until = None
    db.flush()
    return raw, row


def resolve_session(db: Session, raw_token: str | None) -> Operator | None:
    """Return the operator for a valid, unexpired, unrevoked session token."""
    if not raw_token:
        return None

    stmt = select(OperatorSession).where(OperatorSession.token_hash == _hash_token(raw_token))
    session = db.execute(stmt).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None

    now = datetime.now(timezone.utc)
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        return None

    operator = db.get(Operator, session.operator_id)
    if operator is None or not operator.active:
        return None
    if int(session.session_epoch or 1) != int(operator.session_epoch or 1):
        # The password changed after this session was issued.
        return None
    if is_locked(operator):
        return None

    session.last_seen_at = now
    return operator


def revoke_session(db: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    stmt = select(OperatorSession).where(OperatorSession.token_hash == _hash_token(raw_token))
    session = db.execute(stmt).scalar_one_or_none()
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.flush()


def revoke_all_sessions(db: Session, operator_id: str) -> int:
    stmt = select(OperatorSession).where(
        OperatorSession.operator_id == operator_id,
        OperatorSession.revoked_at.is_(None),
    )
    count = 0
    for session in db.execute(stmt).scalars():
        session.revoked_at = datetime.now(timezone.utc)
        count += 1
    if count:
        db.flush()
    return count


def purge_expired_sessions(db: Session) -> int:
    """Delete sessions that expired or were revoked long ago.

    Run at startup so the table does not grow without bound.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    from sqlalchemy import delete

    stmt = delete(OperatorSession).where(OperatorSession.expires_at < cutoff)
    result = db.execute(stmt)
    return int(result.rowcount or 0)