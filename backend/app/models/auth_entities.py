"""Operator account and browser session models.

Design note:

This application is operated by a single person. The table is named
`operators` rather than `users` because there is no registration, no
self-service signup and no multi-tenancy - adding a second row would require
real authorisation work that this model deliberately does not pretend to have.

Two tables:

* `operators` - one row, holding an Argon2id password hash. There is NO default
  password and NO seeded row: the operator is created on first boot from
  `APP_ADMIN_PASSWORD`, and if that variable is unset no operator exists and
  login is unavailable rather than open.

* `operator_sessions` - one row per signed-in browser. Only a SHA-256 hash of
  the session token is stored, so a database leak cannot be replayed as a live
  login. Sessions live in the database rather than in process memory so they
  survive a redeploy, which on Render happens on every push.

Both tables are created by `create_all` at startup like every other table in
this project, so there is no separate migration step.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class Operator(TimestampMixin, Base):
    """The single human who operates this instance."""

    __tablename__ = "operators"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    # Argon2id PHC string, e.g. "$argon2id$v=19$m=65536,t=3,p=4$...".
    # Never the plaintext, never reversible.
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Bumped to invalidate every existing session (used after a password change).
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperatorSession(TimestampMixin, Base):
    """One signed-in browser.

    `token_hash` is SHA-256 of the raw cookie value. The raw token exists only
    in the user's browser, so read access to this table does not grant a login.
    """

    __tablename__ = "operator_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Copied from the operator at issue time, so rotating the epoch kills every
    # session without needing a join.
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Audit trail only. Not used for enforcement - an attacker behind a proxy
    # can control this header.
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)