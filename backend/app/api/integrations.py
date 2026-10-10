"""Integration management endpoints.

Everything the Settings screen needs to add, edit, test, sync, enable/disable
and remove a provider connection - with no restart, no code change and no n8n
edit.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import ApiKeyDep, DbDep, SettingsDep
from app.models.entities import Channel, Integration
from app.schemas.integration import (
    ChannelSyncOut,
    ChannelSyncRequest,
    ConnectionTestOut,
    DiscoverOut,
    IntegrationCreate,
    IntegrationOut,
    IntegrationUpdate,
)
from app.services.integration_service import IntegrationService
from app.services.errors import AppError, ErrorCode

router = APIRouter(prefix="/integrations", tags=["integrations"])


def get_service(db: DbDep, settings: SettingsDep) -> IntegrationService:
    return IntegrationService(db, settings)


ServiceDep = Annotated[IntegrationService, Depends(get_service)]


# --------------------------------------------------------------------------- #
# serialization
# --------------------------------------------------------------------------- #
def _organizations(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [o for o in parsed if isinstance(o, dict) and o.get("id")]


def serialize(integration: Integration, service: IntegrationService) -> IntegrationOut:
    channels = service.channels_for(integration.id)
    return IntegrationOut(
        id=integration.id,
        provider=integration.provider,
        display_name=integration.display_name,
        enabled=integration.enabled,
        status=integration.status,
        credential_hint=integration.credential_hint,
        credential_stored=bool(integration.credential_encrypted),
        external_organization_id=integration.external_organization_id,
        external_organization_name=integration.external_organization_name,
        available_organizations=_organizations(integration.discovered_organizations_json),
        channel_count=len(channels),
        active_channel_count=sum(1 for c in channels if c.active),
        last_tested_at=integration.last_tested_at,
        last_sync_at=integration.last_sync_at,
        last_error_code=integration.last_error_code,
        last_error_message=integration.last_error_message,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
    )


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[IntegrationOut], dependencies=[ApiKeyDep])
def list_integrations(
    service: ServiceDep,
    provider: Annotated[str | None, Query(max_length=32)] = None,
) -> list[IntegrationOut]:
    return [serialize(i, service) for i in service.list_integrations(provider)]


@router.post(
    "",
    response_model=IntegrationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[ApiKeyDep],
)
def create_integration(payload: IntegrationCreate, service: ServiceDep) -> IntegrationOut:
    if not service.encryption_available():
        raise AppError(ErrorCode.INTEGRATION_CREDENTIAL_UNREADABLE, 503, field="api_key")

    integration = service.create(
        display_name=payload.display_name,
        api_key=payload.api_key,
        organization_id=payload.organization_id,
        provider=payload.provider,
        enabled=payload.enabled,
        test_now=payload.test_connection,
    )
    return serialize(integration, service)


@router.get("/{integration_id}", response_model=IntegrationOut, dependencies=[ApiKeyDep])
def get_integration(integration_id: str, service: ServiceDep) -> IntegrationOut:
    return serialize(service.get(integration_id), service)


@router.patch("/{integration_id}", response_model=IntegrationOut, dependencies=[ApiKeyDep])
def update_integration(
    integration_id: str, payload: IntegrationUpdate, service: ServiceDep
) -> IntegrationOut:
    integration = service.get(integration_id)
    if payload.api_key is not None and not service.encryption_available():
        raise AppError(ErrorCode.INTEGRATION_CREDENTIAL_UNREADABLE, 503, field="api_key")

    updated = service.update(
        integration,
        display_name=payload.display_name,
        enabled=payload.enabled,
        api_key=payload.api_key,
        organization_id=payload.organization_id,
    )
    return serialize(updated, service)


@router.post("/{integration_id}/rotate", response_model=IntegrationOut, dependencies=[ApiKeyDep])
def rotate_credential(
    integration_id: str, payload: IntegrationCreate, service: ServiceDep
) -> IntegrationOut:
    """Replace the stored token without touching any other setting."""
    integration = service.get(integration_id)
    rotated = service.rotate_credential(integration, payload.api_key)
    return serialize(rotated, service)


@router.delete(
    "/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[ApiKeyDep],
)
def delete_integration(integration_id: str, service: ServiceDep) -> Response:
    """Disconnect. Imported channels stay but stop being publishable."""
    service.delete(service.get(integration_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# connection testing
# --------------------------------------------------------------------------- #
@router.post(
    "/{integration_id}/test", response_model=ConnectionTestOut, dependencies=[ApiKeyDep]
)
def test_integration(integration_id: str, service: ServiceDep) -> ConnectionTestOut:
    """Real check against the provider. The returned status is what was observed."""
    integration = service.get(integration_id)
    tested = service.test(integration)
    ok = tested.status == "connected"
    return ConnectionTestOut(
        ok=ok,
        status=tested.status,
        integration_id=tested.id,
        organization_id=tested.external_organization_id,
        organization_name=tested.external_organization_name,
        message=tested.last_error_message
        or f"Connected to {tested.external_organization_name or 'Buffer'}.",
        checked_at=tested.last_tested_at or datetime.now(timezone.utc),
    )


@router.get(
    "/{integration_id}/organizations",
    response_model=list[dict],
    dependencies=[ApiKeyDep],
)
def list_organizations(integration_id: str, service: ServiceDep) -> list[dict]:
    """Every Buffer workspace this token can reach."""
    return service.list_available_organizations(service.get(integration_id))


# --------------------------------------------------------------------------- #
# channel discovery + sync
# --------------------------------------------------------------------------- #
@router.post("/{integration_id}/discover", response_model=DiscoverOut, dependencies=[ApiKeyDep])
def discover_channels(integration_id: str, service: ServiceDep) -> DiscoverOut:
    """Query Buffer for the channels in this workspace.

    This is a live API call - the list is never invented locally.
    """
    integration = service.get(integration_id)
    channels = service.discover_channels(integration)
    return DiscoverOut(
        integration_id=integration.id,
        organization_id=integration.external_organization_id or "",
        organization_name=integration.external_organization_name,
        channels=channels,  # type: ignore[arg-type]
        total=len(channels),
        importable=sum(1 for c in channels if not c["locked"] and not c["disconnected"]),
    )


@router.post("/{integration_id}/sync", response_model=ChannelSyncOut, dependencies=[ApiKeyDep])
def sync_channels(
    integration_id: str, payload: ChannelSyncRequest, service: ServiceDep
) -> ChannelSyncOut:
    """Import the chosen channels and make them available app-wide."""
    integration = service.get(integration_id)
    result = service.sync_channels(
        integration,
        account_id=payload.account_id,
        external_channel_ids=payload.external_channel_ids,
        activate=payload.activate,
    )

    from app.models.entities import Account  # local import avoids a cycle

    rows = service._db.scalars(  # noqa: SLF001 - read-back for the response
        select(Channel).where(Channel.account_id == payload.account_id)
    ).all()
    channel_ids = [
        c.id
        for c in rows
        if c.integration_id == integration.id and c.external_channel_id in payload.external_channel_ids
    ]

    return ChannelSyncOut(
        integration_id=integration.id,
        account_id=payload.account_id,
        created=result["created"],
        updated=result["updated"],
        skipped=result["skipped"],
        channel_ids=channel_ids,
        synced_at=integration.last_sync_at or datetime.now(timezone.utc),
    )
