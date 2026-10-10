"""Integration and channel-onboarding service.

This is what turns "add another Buffer account" from a code change into a row.

Flow the UI drives:
    create integration  ->  test connection  ->  discover channels
                        ->  assign channels to a brand  ->  sync

Everything is keyed on Buffer's own identifiers, so syncing twice is idempotent:
the second run updates the existing row instead of creating a duplicate.
"""
from __future__ import annotations

import json
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.entities import Account, Channel, Integration
from app.services.buffer_client import (
    SUPPORTED_PLATFORMS,
    BufferApiError,
    BufferChannel,
    BufferClient,
)
from app.services.credentials import EncryptionUnavailable, build_cipher, hint_for
from app.services.errors import AppError, ErrorCode

logger = get_logger("app.integrations")

#: Integration lifecycle states surfaced to the UI. All are derived from a real
#: probe or a real sync - never from "the field is non-empty".
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_CONNECTED = "connected"
STATUS_CONNECTION_FAILED = "connection_failed"
STATUS_DISABLED = "disabled"
STATUS_REAUTH_REQUIRED = "reauth_required"
STATUS_SYNC_REQUIRED = "sync_required"


def new_integration_id() -> str:
    return f"int_{secrets.token_hex(8)}"


def new_channel_id(account_id: str, external_channel_id: str) -> str:
    """Deterministic internal id for one external profile.

    Derived from the external id ALONE, deliberately not from the account id:
    that way the same Buffer profile always maps to the same local row, so a
    second import is recognised as an update (or as an illegal move to another
    brand) instead of silently creating a duplicate.

    `account_id` is accepted for call-site readability but deliberately unused.
    """
    fingerprint = hashlib.sha256(external_channel_id.encode("utf-8")).hexdigest()[:12]
    return f"ch_{fingerprint}"


class IntegrationService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._cipher = build_cipher(settings)

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def list_integrations(self, provider: str | None = None) -> list[Integration]:
        stmt = select(Integration).order_by(Integration.created_at)
        if provider:
            stmt = stmt.where(Integration.provider == provider)
        return list(self._db.scalars(stmt).all())

    def get(self, integration_id: str) -> Integration:
        integration = self._db.get(Integration, integration_id)
        if integration is None:
            raise AppError(ErrorCode.NOT_FOUND, 404, field="integration_id")
        return integration

    def encryption_available(self) -> bool:
        return self._cipher.available

    def channels_for(self, integration_id: str) -> list[Channel]:
        return list(
            self._db.scalars(select(Channel).where(Channel.integration_id == integration_id)).all()
        )

    # ------------------------------------------------------------------ #
    # credentials
    # ------------------------------------------------------------------ #
    def _encrypt(self, api_key: str) -> tuple[str, str | None]:
        """Store ciphertext + a masked hint. The plaintext is never persisted."""
        return self._cipher.encrypt(api_key), hint_for(api_key)

    def _client_for(self, integration: Integration) -> BufferClient:
        """Resolve the stored credential and build a client for it.

        The token is decrypted into memory only. It is never placed in a
        response, a log line, or an n8n payload.
        """
        try:
            api_key = self._cipher.decrypt(integration.credential_encrypted)
        except EncryptionUnavailable as exc:
            raise AppError(
                ErrorCode.INTEGRATION_CREDENTIAL_UNREADABLE,
                409,
                field="credential",
                details=[{"reason": str(exc)}],
            ) from exc
        return BufferClient(api_key, self._settings)

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def create(
        self,
        *,
        display_name: str,
        api_key: str,
        organization_id: str | None = None,
        provider: str = "buffer",
        enabled: bool = True,
        test_now: bool = True,
    ) -> Integration:
        """Add an integration and, by default, verify it immediately.

        Testing on create means the stored status is real from the very first
        render instead of optimistic.
        """
        api_key = (api_key or "").strip()
        if not api_key:
            raise AppError(ErrorCode.INTEGRATION_CREDENTIAL_REQUIRED, 400, field="api_key")

        # Same workspace already configured under the same name: say so
        # clearly instead of letting the database raise a 500.
        for existing in self.list_integrations(provider):
            if (
                existing.display_name.strip().lower() == (display_name or "Buffer").strip().lower()
                and (organization_id is None or existing.external_organization_id == organization_id)
            ):
                raise AppError(
                    ErrorCode.INTEGRATION_DUPLICATE,
                    409,
                    field="display_name",
                    details=[{"integration_id": existing.id}],
                )

        encrypted, hint = self._encrypt(api_key)

        integration = Integration(
            id=new_integration_id(),
            provider=provider,
            display_name=(display_name or "Buffer").strip()[:160],
            credential_encrypted=encrypted,
            credential_hint=hint,
            external_organization_id=organization_id,
            enabled=enabled,
            status=STATUS_NOT_CONFIGURED,
        )
        self._db.add(integration)

        if test_now:
            # Probe before committing so a bad token never becomes a saved row
            # that merely looks configured. The row is kept, but marked failed,
            # so the user can fix it in place.
            self._probe(integration, organization_id)

        self._db.commit()
        self._db.refresh(integration)
        return integration

    def update(
        self,
        integration: Integration,
        *,
        display_name: str | None = None,
        enabled: bool | None = None,
        api_key: str | None = None,
        organization_id: str | None = None,
        organization_name: str | None = None,
    ) -> Integration:
        if display_name is not None:
            integration.display_name = display_name.strip()[:160]
        if enabled is not None:
            integration.enabled = enabled
            # Enabling an integration that was switched off is a real change of
            # state, so drop it back to "not configured" until it is retested.
            if enabled and integration.status == STATUS_DISABLED:
                integration.status = STATUS_NOT_CONFIGURED
            elif not enabled:
                integration.status = STATUS_DISABLED
        if organization_id is not None:
            integration.external_organization_id = organization_id
        if organization_name is not None:
            integration.external_organization_name = organization_name
        if api_key is not None:
            api_key = api_key.strip()
            if not api_key:
                raise AppError(ErrorCode.INTEGRATION_CREDENTIAL_REQUIRED, 400, field="api_key")
            integration.credential_encrypted, integration.credential_hint = self._encrypt(api_key)
            # A new token invalidates every previous verdict.
            integration.status = STATUS_NOT_CONFIGURED
            integration.last_error_code = None
            integration.last_error_message = None
            integration.last_tested_at = None

        self._db.commit()
        self._db.refresh(integration)
        return integration

    def rotate_credential(self, integration: Integration, api_key: str) -> Integration:
        """Credential rotation, without touching any other field.

        The new token is verified immediately: the whole point of rotating is
        to know whether the replacement works. Status is reset first so a failed
        probe can never be mistaken for the old token's success.
        """
        api_key = (api_key or "").strip()
        if not api_key:
            raise AppError(ErrorCode.INTEGRATION_CREDENTIAL_REQUIRED, 400, field="api_key")

        integration.credential_encrypted, integration.credential_hint = self._encrypt(api_key)
        integration.status = STATUS_NOT_CONFIGURED
        integration.last_error_code = None
        integration.last_error_message = None
        integration.last_tested_at = None

        self._probe(integration)
        self._db.commit()
        self._db.refresh(integration)
        return integration

    def delete(self, integration: Integration) -> None:
        """Disconnect. Channels survive, but stop being publishable.

        Channels keep their account binding (so brands keep their structure)
        and are marked disconnected rather than deleted.
        """
        for channel in self.channels_for(integration.id):
            channel.connection_status = "disconnected"
            channel.integration_id = None
        self._db.delete(integration)
        self._db.commit()

    # ------------------------------------------------------------------ #
    # connection testing
    # ------------------------------------------------------------------ #
    def _probe(self, integration: Integration, organization_id: str | None = None) -> None:
        """Run a real check and record the outcome. Never raises."""
        now = datetime.now(timezone.utc)
        try:
            organizations = self._client_for(integration).test_connection()
        except BufferApiError as exc:
            integration.status = (
                STATUS_REAUTH_REQUIRED if exc.code == "BUFFER_AUTH_ERROR" else STATUS_CONNECTION_FAILED
            )
            integration.last_error_code = exc.code
            integration.last_error_message = exc.message
            integration.last_tested_at = now
            logger.info(
                "integration.test.failed",
                extra={"context": {"integration_id": integration.id, "code": exc.code}},
            )
            return
        except AppError as exc:
            integration.status = STATUS_CONNECTION_FAILED
            integration.last_error_code = exc.code.value if hasattr(exc.code, "value") else str(exc.code)
            integration.last_error_message = exc.error_message
            integration.last_tested_at = now
            return

        if not organizations:
            integration.status = STATUS_CONNECTION_FAILED
            integration.last_error_code = "BUFFER_NO_ORGANIZATIONS"
            integration.last_error_message = (
                "The token is valid but it cannot see any Buffer workspace."
            )
            integration.last_tested_at = now
            return

        chosen_id = organization_id or integration.external_organization_id
        match = next((o for o in organizations if o.id == chosen_id), None)
        if match is None:
            # No explicit choice yet: bind to the only workspace, or the first
            # one when several exist (the UI forces a choice in that case).
            match = organizations[0]
        elif len(organizations) > 1 and organization_id is None:
            match = organizations[0]

        integration.external_organization_id = match.id
        integration.external_organization_name = match.name
        integration.status = STATUS_CONNECTED
        integration.last_error_code = None
        integration.last_error_message = None
        integration.last_tested_at = now
        # Cached so the UI can offer a workspace switcher without another call.
        integration.discovered_organizations_json = json.dumps(
            [{"id": o.id, "name": o.name} for o in organizations]
        )
        logger.info(
            "integration.test.ok",
            extra={"context": {"integration_id": integration.id, "organization": match.id}},
        )

    def test(self, integration: Integration) -> Integration:
        self._probe(integration)
        self._db.commit()
        self._db.refresh(integration)
        return integration

    def list_available_organizations(self, integration: Integration) -> list[dict[str, str]]:
        """Workspaces this token can reach, for the UI's workspace picker."""
        try:
            orgs = self._client_for(integration).test_connection()
        except BufferApiError as exc:
            raise AppError(map_buffer_error(exc), 502, field="integration_id") from exc
        except AppError:
            raise
        return [{"id": o.id, "name": o.name} for o in orgs]

    # ------------------------------------------------------------------ #
    # channel discovery + sync
    # ------------------------------------------------------------------ #
    def discover_channels(self, integration: Integration) -> list[dict[str, Any]]:
        """Ask Buffer what this workspace actually has. No local guesswork."""
        if not integration.enabled:
            raise AppError(ErrorCode.INTEGRATION_DISABLED, 409, field="integration_id")
        if not integration.external_organization_id:
            raise AppError(
                ErrorCode.INTEGRATION_NOT_TESTED, 409, field="integration_id"
            )

        try:
            channels = self._client_for(integration).list_channels(
                integration.external_organization_id
            )
        except BufferApiError as exc:
            self._record_sync_failure(integration, exc)
            self._db.commit()
            raise AppError(map_buffer_error(exc), 502, field="integration_id") from exc

        integration.last_sync_at = datetime.now(timezone.utc)
        if integration.status in (STATUS_NOT_CONFIGURED, STATUS_SYNC_REQUIRED):
            integration.status = STATUS_CONNECTED
        self._db.commit()

        existing = {c.external_channel_id: c for c in self.channels_for(integration.id)}
        return [self._describe(c, existing.get(c.id)) for c in channels]

    def _describe(self, channel: BufferChannel, existing: Channel | None) -> dict[str, Any]:
        return {
            "external_channel_id": channel.id,
            "service": channel.service,
            "name": channel.name,
            "display_name": channel.display_name or channel.name,
            "descriptor": channel.descriptor,
            "channel_type": channel.channel_type,
            "avatar": channel.avatar,
            "external_link": channel.external_link,
            "timezone": channel.timezone,
            "disconnected": channel.disconnected,
            "locked": channel.locked,
            "queue_paused": channel.queue_paused,
            "supported": channel.service in SUPPORTED_PLATFORMS,
            # Present when this Buffer channel is already onboarded locally.
            "local_channel_id": existing.id if existing else None,
            "local_account_id": existing.account_id if existing else None,
            "already_imported": existing is not None,
        }

    def sync_channels(
        self,
        integration: Integration,
        *,
        account_id: str,
        external_channel_ids: list[str],
        activate: bool = True,
    ) -> dict[str, Any]:
        """Import the selected Buffer channels as local channel rows.

        Idempotent: re-running updates the same rows. Only the requested ids are
        imported, so discovering every channel never silently widens what the
        application can publish to.
        """
        account = self._db.get(Account, account_id)
        if account is None:
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, 404, field="account_id")
        if not integration.enabled:
            raise AppError(ErrorCode.INTEGRATION_DISABLED, 409, field="integration_id")
        if not integration.external_organization_id:
            raise AppError(ErrorCode.INTEGRATION_NOT_TESTED, 409, field="integration_id")

        wanted = [c.strip() for c in external_channel_ids if c and c.strip()]
        if not wanted:
            raise AppError(ErrorCode.INTEGRATION_NO_CHANNELS_SELECTED, 400, field="external_channel_ids")

        try:
            remote = {
                c.id: c
                for c in self._client_for(integration).list_channels(
                    integration.external_organization_id
                )
            }
        except BufferApiError as exc:
            self._record_sync_failure(integration, exc)
            self._db.commit()
            raise AppError(map_buffer_error(exc), 502, field="external_channel_ids") from exc

        created, updated, skipped = 0, 0, 0
        sort_order = self._next_sort_order(account_id)

        for external_id in wanted:
            buffer_channel = remote.get(external_id)
            if buffer_channel is None:
                skipped += 1
                continue

            local_id = new_channel_id(account_id, external_id)
            row = self._db.get(Channel, local_id)

            if row is None:
                row = Channel(id=local_id, account_id=account_id, platform=buffer_channel.service)
                self._db.add(row)
                created += 1
            elif row.account_id != account_id:
                # Never move a channel between accounts silently: that would
                # silently republish a brand's history under another brand.
                raise AppError(
                    ErrorCode.CHANNEL_ALREADY_ASSIGNED,
                    409,
                    field="external_channel_ids",
                    details=[{"channel_id": row.id, "account_id": row.account_id}],
                )
            else:
                updated += 1

            self._apply_remote(row, buffer_channel, integration, activate=activate)
            sort_order += 1

        integration.last_sync_at = datetime.now(timezone.utc)
        if integration.status in (STATUS_NOT_CONFIGURED, STATUS_SYNC_REQUIRED):
            integration.status = STATUS_CONNECTED

        self._db.commit()

        logger.info(
            "integration.sync",
            extra={
                "context": {
                    "integration_id": integration.id,
                    "account_id": account_id,
                    "created": created,
                    "updated": updated,
                    "skipped": skipped,
                }
            },
        )
        return {"created": created, "updated": updated, "skipped": skipped}

    def _apply_remote(
        self,
        row: Channel,
        buffer_channel: BufferChannel,
        integration: Integration,
        *,
        activate: bool,
    ) -> None:
        row.integration_id = integration.id
        row.external_channel_id = buffer_channel.id
        row.external_service = buffer_channel.service
        row.external_username = buffer_channel.name or None
        row.platform = buffer_channel.service
        row.display_name = buffer_channel.display_name or buffer_channel.name or buffer_channel.id
        if buffer_channel.avatar:
            row.avatar = buffer_channel.avatar
        row.is_locked = buffer_channel.locked
        row.queue_paused = buffer_channel.queue_paused
        row.capabilities_json = json.dumps(["text", buffer_channel.service])
        row.last_synced_at = datetime.now(timezone.utc)

        if buffer_channel.disconnected:
            row.connection_status = "reauth_required"
        elif buffer_channel.locked:
            row.connection_status = "locked"
        else:
            row.connection_status = "connected"

        if activate:
            row.active = True

    def _next_sort_order(self, account_id: str) -> int:
        current = self._db.scalar(
            select(func.coalesce(func.max(Channel.sort_order), -1)).where(
                Channel.account_id == account_id
            )
        )
        return int(current or 0) + 1

    def _record_sync_failure(self, integration: Integration, exc: BufferApiError) -> None:
        integration.status = (
            STATUS_REAUTH_REQUIRED if exc.code == "BUFFER_AUTH_ERROR" else STATUS_CONNECTION_FAILED
        )
        integration.last_error_code = exc.code
        integration.last_error_message = exc.message
        integration.last_tested_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    # publishing support
    # ------------------------------------------------------------------ #
    def resolve_publish_target(self, channel: Channel) -> tuple[BufferClient, str]:
        """Integration + Buffer channel id for a channel, or a precise error.

        Called on every publish, so this is where "a disconnected integration
        must not publish silently" is actually enforced.
        """
        if not channel.external_channel_id:
            raise AppError(
                ErrorCode.CHANNEL_NOT_BOUND,
                409,
                field="channel_id",
                details=[{"reason": "This channel is not linked to a Buffer channel."}],
            )
        if not channel.integration_id:
            raise AppError(
                ErrorCode.CHANNEL_NOT_BOUND,
                409,
                field="channel_id",
                details=[{"reason": "This channel has no integration. Connect it in Settings."}],
            )

        integration = self._db.get(Integration, channel.integration_id)
        if integration is None:
            raise AppError(ErrorCode.INTEGRATION_NOT_FOUND, 409, field="channel_id")
        if not integration.enabled:
            raise AppError(
                ErrorCode.INTEGRATION_DISABLED,
                409,
                field="channel_id",
                details=[{"reason": f"Integration '{integration.display_name}' is disabled."}],
            )
        if integration.status == STATUS_REAUTH_REQUIRED:
            raise AppError(
                ErrorCode.INTEGRATION_REAUTH_REQUIRED,
                409,
                field="channel_id",
                details=[{"reason": f"Integration '{integration.display_name}' needs to be reconnected."}],
            )
        if channel.connection_status == "reauth_required":
            raise AppError(
                ErrorCode.CHANNEL_REAUTH_REQUIRED,
                409,
                field="channel_id",
                details=[{"reason": "Buffer reports this channel as disconnected."}],
            )
        if channel.is_locked:
            raise AppError(
                ErrorCode.CHANNEL_LOCKED,
                409,
                field="channel_id",
                details=[{"reason": "Buffer has this channel locked. Locked channels cannot receive posts."}],
            )

        return self._client_for(integration), channel.external_channel_id


def map_buffer_error(exc: BufferApiError) -> ErrorCode:
    """Single mapping from a Buffer failure to one of our stable error codes."""
    mapping = {
        "BUFFER_AUTH_ERROR": ErrorCode.BUFFER_AUTH_ERROR,
        "BUFFER_RATE_LIMIT": ErrorCode.BUFFER_RATE_LIMIT,
        "BUFFER_SERVER_ERROR": ErrorCode.BUFFER_SERVER_ERROR,
        "BUFFER_NETWORK_ERROR": ErrorCode.BUFFER_NETWORK_ERROR,
        "BUFFER_TIMEOUT": ErrorCode.PUBLISHING_TIMEOUT,
        "BUFFER_BAD_REQUEST": ErrorCode.BUFFER_BAD_REQUEST,
        "BUFFER_POST_FAILED": ErrorCode.BUFFER_POST_FAILED,
        "BUFFER_ORGANIZATION_NOT_FOUND": ErrorCode.BUFFER_ORGANIZATION_NOT_FOUND,
        "BUFFER_BAD_RESPONSE": ErrorCode.BUFFER_BAD_RESPONSE,
    }
    return mapping.get(exc.code, ErrorCode.PUBLISHING_FAILED)
