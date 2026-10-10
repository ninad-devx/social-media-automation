"""ORM models.

Design note (changed from the original "credentials live only in n8n" model):
Buffer credentials and the external Buffer channel ids are now stored HERE,
encrypted, so the backend is the single source of truth and the n8n workflow no
longer carries an account registry. The publishing path is unchanged for
existing users: `PUBLISH_ENGINE=n8n` (the default) still hands the request to
n8n exactly as before.

Credentials are stored as Fernet ciphertext via `app.services.crypto` and are
never serialised by any schema in `app.schemas`.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False, default="brand")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    channels: Mapped[list["Channel"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        order_by="Channel.sort_order",
        lazy="selectin",
    )


class Integration(TimestampMixin, Base):
    """One configured connection to an external provider (e.g. a Buffer workspace).

    A single Buffer API key can see several organizations ("workspaces"), so one
    row represents one organization under one credential. Several integrations
    may therefore point at the same credential and different brands can each own
    their own integration.

    `credential_encrypted` is Fernet ciphertext. It is never returned by an API
    response - see `app.schemas.integration.IntegrationOut`.
    """

    __tablename__ = "integrations"
    __table_args__ = (
        # Deliberately NOT unique: one Buffer workspace can legitimately be
        # reachable by several tokens (different teams, a personal token plus
        # an agency one). A hard constraint here surfaced as a 500 on a valid
        # configuration, so duplicate detection is a friendly check in the
        # service layer instead.
        Index("ix_integrations_provider", "provider"),
        Index("ix_integrations_status", "status"),
        Index("ix_integrations_provider_org", "provider", "external_organization_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Registry slug, e.g. "buffer".
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # User-facing label, so several keys can point at the same provider.
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Fernet ciphertext. NEVER returned by the API.
    credential_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Masked tail only, e.g. "…f3a9", so the UI can show *that* a key is stored
    # without ever receiving the key.
    credential_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Buffer organization ("workspace") this integration is bound to. Null until
    # the credential has been tested successfully.
    external_organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_organization_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # JSON list of every workspace this token can reach, cached at test time so
    # the UI can offer a workspace switcher without another network call.
    discovered_organizations_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # not_configured | connected | connection_failed | disabled | reauth_required | sync_required
    # Derived from real probe/sync results, never from "is the field non-empty".
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_configured")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Safe, user-facing message. Never contains a credential.
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channels: Mapped[list["Channel"]] = relationship(
        back_populates="integration",
        lazy="selectin",
    )


class Channel(TimestampMixin, Base):
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_channels_account_id_id"),
        Index("ix_channels_account_id", "account_id"),
        Index("ix_channels_platform", "platform"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str | None] = mapped_column(String(160), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- external binding -------------------------------------------------
    # Which integration this channel is published through. Nullable so that
    # every pre-existing row keeps working after the migration.
    integration_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("integrations.id", ondelete="SET NULL"), nullable=True
    )
    # Buffer's own channel id. Buffer calls these "channels" (the legacy REST
    # API called them "profiles"). Required for publishing.
    external_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Buffer's `service` value, e.g. "instagram". Kept separate from `platform`
    # so a future provider rename does not break existing history rows.
    external_service: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The account's handle as Buffer reports it, kept apart from the internal
    # `username` we may have written ourselves.
    external_username: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # connected | disconnected | locked | unknown - mirrored from Buffer.
    connection_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    # Locked channels cannot be posted to (plan downgrade).
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Paused queues silently swallow scheduled posts.
    queue_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # JSON list of capabilities Buffer reports, e.g. ["text","image"].
    capabilities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship(back_populates="channels")
    integration: Mapped[Integration | None] = relationship(back_populates="channels")


class PublishLog(TimestampMixin, Base):
    """Foundation for the Posts / publishing history page."""

    __tablename__ = "publish_logs"
    __table_args__ = (Index("ix_publish_logs_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    n8n_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_preview: Mapped[str] = mapped_column(String(280), nullable=False, default="")
    media_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    publish_mode: Mapped[str] = mapped_column(String(16), nullable=False)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Dhaka")

    # pending | published | scheduled | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    buffer_publication_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_link: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="dashboard")


class MediaAsset(TimestampMixin, Base):
    """Metadata for an uploaded file.

    The bytes live in the storage backend, not here. This table is the
    foundation for a media library: it records what exists, where it is, and
    whether Buffer can actually download it.
    """

    __tablename__ = "media_assets"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    # False means the URL is local-only and Buffer cannot fetch it.
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class IdempotencyRecord(TimestampMixin, Base):
    """Stores the outcome of a request keyed by Idempotency-Key.

    Prevents an accidental double submit from publishing the same post twice.
    """

    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)