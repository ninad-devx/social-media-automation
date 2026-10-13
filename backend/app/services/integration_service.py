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
    """Deterministic internal id for one external profile *within one brand*.

    Derived from the account id AND the external id. That combination is what
    makes "move this Buffer profile to a different brand" expressible: the two
    assignments get different local ids, so releasing one by archiving it frees
    the profile for the other without either row having to be rewritten.

    Earlier this hashed the external id alone, which pinned every Buffer profile
    to one brand permanently - a channel that had once been imported could never
    be attached anywhere else, and there was no unlink to release it.

    Existing rows are untouched: their ids were computed by the old formula and
    nothing recomputes them. A re-import into the *same* brand finds the
    existing row by `external_channel_id`, not by regenerating this id.
    """
    fingerprint = hashlib.sha256(f"{account_id}\x00{external_channel_id}".encode("utf-8")).hexdigest()[:12]
    return f"ch_{fingerprint}"


def find_channel_by_external(db: Session, external_channel_id: str) -> Channel | None:
    """The live local row that currently claims a Buffer profile, if any."""
    if not external_channel_id:
        return None
    return db.scalars(
        select(Channel).where(
            Channel.external_channel_id == external_channel_id,
            Channel.archived_at.is_(None),
        )
    ).first()


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
            self._db.scalars(
                select(Channel).where(
                    Channel.integration_id == integration_id,
                    Channel.archived_at.is_(None),
                )
            ).all()
        )

    # ------------------------------------------------------------------ #
    # archival
    # ------------------------------------------------------------------ #
    def archive_channel(self, channel: Channel, *, reason: str | None = None) -> Channel:
        """Remove a channel locally. Never touches Buffer.

        Two distinct effects, both deliberate:

        1. `active=False` + `archived_at` so it leaves every selector while the
           row - and therefore the publishing history that references it - stays.
        2. The external assignment is released. `external_channel_id` and
           `integration_id` are cleared, which is what allows the same Buffer
           profile to be imported into a *different* brand later. The unique
           index on `external_channel_id` guarantees no second row can claim it
           in the meantime.

        Nothing is sent to Buffer: this is a local bookkeeping operation, and a
        disconnect in Buffer is a separate, explicit act.
        """
        channel.active = False
        channel.archived_at = datetime.now(timezone.utc)
        channel.archived_reason = (reason or "removed_by_operator")[:64]
        channel.external_channel_id = None
        channel.integration_id = None
        channel.external_service = None
        channel.connection_status = "unknown"
        channel.is_locked = False
        channel.queue_paused = False
        self._db.commit()
        self._db.refresh(channel)
        logger.info(
            "integration.channel_archived",
            extra={
                "context": {
                    "channel_id": channel.id,
                    "account_id": channel.account_id,
                    "reason": channel.archived_reason,
                }
            },
        )
        return channel

    def restore_channel(self, channel: Channel) -> Channel:
        """Undo an archive without re-linking to Buffer.

        The external assignment is not restored, because it was released on
        purpose and re-acquiring it has to go through the normal import flow
        (where ownership is checked). This clears the archive mark so the row
        stops looking removed; the operator then re-imports the profile.
        """
        channel.archived_at = None
        channel.archived_reason = None
        channel.active = True
        self._db.commit()
        self._db.refresh(channel)
        return channel

    # ------------------------------------------------------------------ #
    # credentials
    # ------------------------------------------------------------------ #
    def _encrypt(self, api_key: str) -> tuple[str, str | None]:
        """Store ciphertext + a masked hint. The plaintext is never persisted."""
        return self._cipher.encrypt(api_key), hint_for(api_key)

    def client_for(self, integration: Integration) -> BufferClient:
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
        """Run a real check and record the outcome. Never raises.

        Read-only with respect to the integration's own configuration: it may
        fill in `external_organization_id` when the integration has never been
        bound, but it must never move an integration that already has an
        explicit workspace selection. The previous implementation ended with an
        `elif len(organizations) > 1 ... match = organizations[0]`, so pressing
        "Test connection" silently re-pointed a multi-workspace token at
        whichever workspace Buffer happened to list first.
        """
        now = datetime.now(timezone.utc)

        existing_choice = integration.external_organization_id
        # An explicit `organization_id` argument (create/update) is a deliberate
        # operator choice and always wins.
        explicit_choice = organization_id or existing_choice

        try:
            organizations = self.client_for(integration).test_connection()
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

        known_ids = {o.id for o in organizations}
        match = next((o for o in organizations if o.id == explicit_choice), None)

        # Cache what the token can reach BEFORE any early return, so the UI's
        # workspace picker has the list even when the integration cannot be
        # bound yet because a choice is still required.
        integration.discovered_organizations_json = json.dumps(
            [{"id": o.id, "name": o.name} for o in organizations]
        )

        if explicit_choice and match is None:
            # The saved workspace is no longer visible with this token. That is
            # a real problem the operator has to resolve, so it is reported
            # rather than papered over by picking a different workspace.
            integration.status = STATUS_SYNC_REQUIRED
            integration.last_error_code = "BUFFER_ORGANIZATION_NOT_FOUND"
            integration.last_error_message = (
                f"Workspace '{explicit_choice}' is not visible with this token any more. "
                "Choose another workspace, or enter a token that can reach it."
            )
            integration.last_tested_at = now
            return

        if match is None:
            # Never bound yet. A single workspace is unambiguous; several means
            # the operator has to choose, so nothing is bound and the UI is told
            # to ask.
            if len(organizations) > 1:
                integration.status = STATUS_NOT_CONFIGURED
                integration.last_error_code = "BUFFER_WORKSPACE_SELECTION_REQUIRED"
                integration.last_error_message = (
                    f"This token can reach {len(organizations)} workspaces. "
                    "Choose which one to publish through in Settings."
                )
                integration.last_tested_at = now
                return
            match = organizations[0]

        integration.external_organization_id = match.id
        integration.external_organization_name = match.name
        integration.status = STATUS_CONNECTED
        integration.last_error_code = None
        integration.last_error_message = None
        integration.last_tested_at = now
        logger.info(
            "integration.test.ok",
            extra={"context": {"integration_id": integration.id, "organization": match.id}},
        )

    def test(self, integration: Integration) -> Integration:
        self._probe(integration)
        self._db.commit()
        self._db.refresh(integration)
        return integration

    def select_organization(self, integration: Integration, organization_id: str) -> Integration:
        """Bind the integration to a workspace the operator chose.

        Verifies the workspace is one this credential can actually reach, so a
        stale or hand-typed id is refused instead of producing an integration
        that fails on every publish.
        """
        if not organization_id or not organization_id.strip():
            raise AppError(ErrorCode.INVALID_REQUEST, 400, field="organization_id")

        try:
            orgs = self.client_for(integration).test_connection()
        except BufferApiError as exc:
            self._record_sync_failure(integration, exc)
            self._db.commit()
            raise AppError(map_buffer_error(exc), 502, field="organization_id") from exc

        match = next((o for o in orgs if o.id == organization_id), None)
        if match is None:
            available = ", ".join(f"{o.name} ({o.id})" for o in orgs) or "none"
            raise AppError(
                ErrorCode.BUFFER_ORGANIZATION_NOT_FOUND,
                400,
                field="organization_id",
                details=[{"available": available}],
            )

        integration.external_organization_id = match.id
        integration.external_organization_name = match.name
        integration.status = STATUS_CONNECTED
        integration.last_error_code = None
        integration.last_error_message = None
        integration.last_tested_at = datetime.now(timezone.utc)
        integration.discovered_organizations_json = json.dumps(
            [{"id": o.id, "name": o.name} for o in orgs]
        )
        self._db.commit()
        self._db.refresh(integration)
        logger.info(
            "integration.workspace_selected",
            extra={"context": {"integration_id": integration.id, "organization": match.id}},
        )
        return integration

    def list_available_organizations(self, integration: Integration) -> list[dict[str, str]]:
        """Workspaces this token can reach, for the UI's workspace picker."""
        try:
            orgs = self.client_for(integration).test_connection()
        except BufferApiError as exc:
            raise AppError(map_buffer_error(exc), 502, field="integration_id") from exc
        except AppError:
            raise
        return [{"id": o.id, "name": o.name} for o in orgs]

    # ------------------------------------------------------------------ #
    # manual channel binding
    # ------------------------------------------------------------------ #
    def bind_channel(
        self,
        *,
        account_id: str,
        integration_id: str,
        external_channel_id: str,
        platform: str,
        display_name: str | None = None,
        username: str | None = None,
    ) -> Channel:
        """Attach a local channel to a specific Buffer profile, by hand.

        This is the fallback when automatic discovery is unavailable. It is
        also the path the audit found was broken: the previous implementation
        wrote `external_channel_id` without `integration_id`, which produced a
        channel that looked linked but could never publish. The fix is not to
        guess an integration - there is no safe guess - so the caller must name
        one explicitly, and the profile is verified to actually exist in that
        workspace before anything is written.

        Order matters: nothing is written until every check has passed, so a
        rejected binding leaves no partial row behind.
        """
        from app.services import platforms as platform_model

        account = self._db.get(Account, account_id)
        if account is None:
            raise AppError(ErrorCode.ACCOUNT_NOT_FOUND, 404, field="account_id")
        if not account.active:
            raise AppError(ErrorCode.ACCOUNT_INACTIVE, 409, field="account_id")

        external_channel_id = (external_channel_id or "").strip()
        if not external_channel_id:
            raise AppError(ErrorCode.INVALID_REQUEST, 400, field="external_channel_id")

        if not (integration_id or "").strip():
            # Never guess. Which integration to publish through is exactly the
            # thing that cannot be inferred.
            raise AppError(
                ErrorCode.INTEGRATION_REQUIRED_FOR_BINDING, 400, field="integration_id"
            )

        integration = self._db.get(Integration, integration_id)
        if integration is None:
            raise AppError(ErrorCode.INTEGRATION_NOT_FOUND, 404, field="integration_id")
        if not integration.enabled:
            raise AppError(ErrorCode.INTEGRATION_DISABLED, 409, field="integration_id")
        if integration.status in (
            STATUS_CONNECTION_FAILED,
            STATUS_DISABLED,
            STATUS_REAUTH_REQUIRED,
        ):
            raise AppError(
                ErrorCode.INTEGRATION_REAUTH_REQUIRED, 409, field="integration_id"
            )
        if not integration.external_organization_id:
            raise AppError(
                ErrorCode.INTEGRATION_NOT_TESTED, 409, field="integration_id"
            )

        requested = (platform or "").strip().lower()
        if not platform_model.known_platform(requested):
            raise AppError(
                ErrorCode.PLATFORM_NOT_SUPPORTED,
                400,
                field="platform",
                details=[{"supported": list(platform_model.PLATFORM_KEYS)}],
            )

        client = self.client_for(integration)

        # The profile must really exist in the workspace the integration is
        # bound to. Without this, a typo would create a channel that only fails
        # at publish time.
        try:
            remote = client.get_channel(
                integration.external_organization_id, external_channel_id
            )
        except BufferApiError as exc:
            self._record_sync_failure(integration, exc)
            self._db.commit()
            raise AppError(map_buffer_error(exc), 502, field="external_channel_id") from exc

        if remote is None:
            raise AppError(
                ErrorCode.CHANNEL_NOT_IN_INTEGRATION,
                404,
                field="external_channel_id",
                details=[{"organization_id": integration.external_organization_id}],
            )

        remote_service = remote.service.strip().lower()
        if remote_service and requested and remote_service != requested:
            raise AppError(
                ErrorCode.PLATFORM_NOT_SUPPORTED,
                400,
                field="platform",
                details=[
                    {
                        "submitted": requested,
                        "buffer_service": remote_service,
                        "reason": "Buffer reports a different platform for this channel.",
                    }
                ],
            )

        local_id = new_channel_id(account_id, external_channel_id)
        existing = self._db.get(Channel, local_id)

        if existing is None:
            existing = find_channel_by_external(self._db, external_channel_id)

        if existing is not None and existing.archived_at is None:
            if existing.account_id != account_id:
                raise AppError(
                    ErrorCode.CHANNEL_ALREADY_ASSIGNED, 409, field="external_channel_id"
                )
            row = existing
        elif existing is not None:
            existing.archived_at = None
            existing.archived_reason = None
            row = existing
        else:
            row = Channel(id=local_id, account_id=account_id, platform=remote_service or requested)
            self._db.add(row)

        row.external_username = username or remote.name or None
        if username:
            row.username = username
        elif remote.name:
            row.username = remote.name
        if display_name:
            row.display_name = display_name.strip()[:120]
        row.archived_at = None
        row.archived_reason = None

        self.apply_remote(row, remote, integration, activate=True)
        # A manual binding never overrides an operator-chosen display name.
        if display_name:
            row.display_name = display_name.strip()[:120]

        self._db.commit()
        self._db.refresh(row)
        logger.info(
            "integration.channel_bound",
            extra={
                "context": {
                    "channel_id": row.id,
                    "account_id": account_id,
                    "integration_id": integration.id,
                    "organization_id": integration.external_organization_id,
                }
            },
        )
        return row

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
            channels = self.client_for(integration).list_channels(
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
                for c in self.client_for(integration).list_channels(
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
                # Another brand may still hold this profile (or an archived row
                # may exist under the old id formula), so look it up by the
                # external id before creating anything.
                row = find_channel_by_external(self._db, external_id)

            if row is None:
                row = Channel(id=local_id, account_id=account_id, platform=buffer_channel.service)
                self._db.add(row)
                created += 1
            elif row.archived_at is not None:
                # Re-importing a profile that was archived earlier on this brand
                # revives the same row rather than creating a second one.
                row.archived_at = None
                row.archived_reason = None
                updated += 1
            elif row.account_id != account_id:
                # Never move a channel between accounts silently: that would
                # silently republish a brand's history under another brand.
                # Reassignment is an explicit operation instead.
                raise AppError(
                    ErrorCode.CHANNEL_ALREADY_ASSIGNED,
                    409,
                    field="external_channel_ids",
                    details=[
                        {
                            "channel_id": row.id,
                            "account_id": row.account_id,
                            "reason": "Archive it on its current brand, or use reassign.",
                        }
                    ],
                )
            else:
                updated += 1

            self.apply_remote(row, buffer_channel, integration, activate=activate)
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

    def apply_remote(
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
        row.capabilities_json = json.dumps(channel_capabilities(buffer_channel.service))
        row.last_synced_at = datetime.now(timezone.utc)
        row.archived_at = None
        row.archived_reason = None

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

        return self.client_for(integration), channel.external_channel_id


def channel_capabilities(platform: str) -> list[str]:
    """What this channel's platform can actually accept.

    The audit found this column being written as ``["text", <platform name>]``:
    the platform name is not a capability, and nothing read the field anyway.
    It is now derived from the single capability model, so it means something
    and stays correct when the matrix changes.
    """
    from app.services import platforms as platform_model

    spec = platform_model.spec_for(platform)
    out = ["text"]
    if spec.supports_image:
        out.append("image")
    if spec.supports_video:
        out.append("video")
    if spec.supports_carousel:
        out.append("carousel")
    out.extend(f"type:{key}" for key in spec.available_post_types())
    return out


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
