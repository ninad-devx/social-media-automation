"""Tests for configuration-driven integrations, channels and publishing.

No real network calls and no real social publishing. Buffer is mocked at the
HTTP boundary so the real GraphQL request/response shapes are exercised.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.entities import Account, Channel, Integration


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
ORG_ID = "org_abc123"

CHANNELS_PAYLOAD: dict[str, Any] = {
    "data": {
        "channels": [
            {
                "id": "chan_instagram_1",
                "name": "glaamora",
                "displayName": "Glaamora",
                "descriptor": "Instagram Profile",
                "service": "instagram",
                "type": "PROFILE",
                "avatar": None,
                "externalLink": "https://instagram.com/glaamora",
                "isDisconnected": False,
                "isLocked": False,
                "isQueuePaused": False,
                "timezone": "Asia/Dhaka",
            },
            {
                "id": "chan_linkedin_1",
                "name": "sortpi",
                "displayName": "SortPi",
                "descriptor": "LinkedIn Page",
                "service": "linkedin",
                "type": "PAGE",
                "avatar": None,
                "externalLink": None,
                "isDisconnected": False,
                "isLocked": False,
                "isQueuePaused": True,
                "timezone": "Europe/London",
            },
            {
                "id": "chan_broken_1",
                "name": "broken",
                "displayName": "Broken",
                "descriptor": "Facebook Page",
                "service": "facebook",
                "type": "PAGE",
                "avatar": None,
                "externalLink": None,
                # Buffer itself reports this one as disconnected.
                "isDisconnected": True,
                "isLocked": False,
                "isQueuePaused": False,
                "timezone": "Europe/London",
            },
        ]
    }
}

ACCOUNT_PAYLOAD: dict[str, Any] = {
    "data": {"account": {"id": "acct_1", "organizations": [{"id": ORG_ID, "name": "Main workspace"}]}}
}


def _body(request: httpx.Request) -> dict:
    """MockTransport hands over a Request, so decode the body manually."""
    try:
        return json.loads(request.content.decode("utf-8"))
    except Exception:
        return {}


def graphql_handler(request: httpx.Request) -> httpx.Response:
    """Mock Buffer's single GraphQL endpoint, dispatching on the query text."""
    assert request.headers["authorization"].startswith("Bearer ")
    body = _body(request)

    if "organizations" in body.get("query", ""):
        return httpx.Response(200, json=ACCOUNT_PAYLOAD)
    if "channels(input" in body.get("query", ""):
        return httpx.Response(200, json=CHANNELS_PAYLOAD)
    if "createPost" in body.get("query", ""):
        channel_id = body["variables"]["input"]["channelId"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "createPost": {
                        "__typename": "PostActionSuccess",
                        "post": {
                            "id": "post_abc",
                            "text": body["variables"]["input"]["text"],
                            "status": "sent",
                            "dueAt": None,
                            "channelId": channel_id,
                            "channelService": "instagram",
                            "externalLink": "https://instagram.com/p/abc",
                            "isCustomScheduled": False,
                            "via": "app",
                            "error": None,
                        },
                    }
                }
            },
        )
    return httpx.Response(400, json={"errors": [{"message": "unexpected operation"}]})


@pytest.fixture()
def buffer_mock(monkeypatch):
    """Route every outbound Buffer call to the mock and record the payloads."""
    from app.services import buffer_client

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        calls.append(body)
        return graphql_handler(request)

    transport = httpx.MockTransport(handler)
    original = buffer_client.BufferClient._http

    def patched(self):
        # Mirror the real client: one instance, reused for the call.
        client = httpx.Client(transport=transport, timeout=5.0)
        object.__setattr__(self, "_client", client)
        return original(self)

    monkeypatch.setattr(buffer_client.BufferClient, "_http", patched)
    return calls


@pytest.fixture()
def brand(db):
    """A brand with no channels, so imports are the only rows present."""
    from app.models.entities import Account

    account = Account(
        id="sortpi",
        name="SortPi",
        type="brand",
        active=True,
        sort_order=9,
    )
    db.add(account)
    db.commit()
    return account


@pytest.fixture()
def direct_engine(monkeypatch):
    """Switch the publish path to the direct Buffer engine.

    Mutates the cached settings object so `monkeypatch` restores it
    automatically after each test, instead of leaking into the next one.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "publish_engine", "direct", raising=False)
    return settings


@pytest.fixture()
def internal_secret(monkeypatch):
    """Enable the internal endpoint with a known secret."""
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_service_secret", "correct-horse", raising=False)
    return "correct-horse"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_integration(client: TestClient, name: str = "Buffer", key: str = "tok_secret_123456") -> dict:
    response = client.post(
        "/api/integrations",
        json={"display_name": name, "api_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Integration lifecycle
# --------------------------------------------------------------------------- #
class TestIntegrationCrud:
    def test_create_tests_connection_and_never_returns_the_token(self, client, buffer_mock):
        body = make_integration(client)

        assert body["status"] == "connected"
        assert body["external_organization_id"] == ORG_ID
        assert body["external_organization_name"] == "Main workspace"
        assert body["credential_stored"] is True

        # The raw token must appear nowhere in the response.
        assert "tok_secret_123456" not in str(body)
        # Only a masked hint is exposed.
        assert body["credential_hint"].endswith("3456")
        assert "tok_secret_123456" not in body["credential_hint"]

    def test_invalid_token_is_stored_but_marked_failed(self, client, monkeypatch):
        from app.services import buffer_client

        def unauthorized(request):
            return httpx.Response(401, json={"errors": [{"message": "Unauthorized"}]})

        transport = httpx.MockTransport(unauthorized)
        monkeypatch.setattr(
            buffer_client.BufferClient,
            "_http",
            lambda self: httpx.Client(transport=transport, timeout=5.0),
        )

        response = client.post(
            "/api/integrations", json={"display_name": "Bad", "api_key": "nope"}
        )
        assert response.status_code == 201
        body = response.json()
        # Status reflects a real probe, not optimism.
        assert body["status"] == "reauth_required"
        assert body["last_error_code"] == "BUFFER_AUTH_ERROR"
        assert "nope" not in str(body)

    def test_enable_disable_transitions_status(self, client, buffer_mock):
        integration = make_integration(client)

        off = client.patch(
            f"/api/integrations/{integration['id']}", json={"enabled": False}
        ).json()
        assert off["enabled"] is False
        assert off["status"] == "disabled"

        on = client.patch(
            f"/api/integrations/{integration['id']}", json={"enabled": True}
        ).json()
        assert on["enabled"] is True
        # Re-enabling invalidates the old verdict until retested.
        assert on["status"] == "not_configured"

    def test_credential_rotation_resets_status_and_keeps_other_settings(self, client, buffer_mock):
        integration = make_integration(client)

        rotated = client.post(
            f"/api/integrations/{integration['id']}/rotate",
            json={"api_key": "brand_new_token_9999"},
        ).json()

        assert rotated["display_name"] == "Buffer"
        assert rotated["status"] == "connected"
        assert rotated["credential_hint"].endswith("9999")
        assert "brand_new_token_9999" not in str(rotated)

    def test_delete_marks_channels_disconnected_but_keeps_them(self, client, buffer_mock, brand):
        integration = make_integration(client)
        client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "sortpi", "external_channel_ids": ["chan_instagram_1"]},
        )

        response = client.delete(f"/api/integrations/{integration['id']}")
        assert response.status_code == 204

        channels = client.get("/api/accounts/sortpi/channels").json()
        assert len(channels) == 1
        # Kept, so the brand structure survives, but no longer publishable.
        assert channels[0]["connection_status"] == "disconnected"
        assert channels[0]["publishable"] is False
        assert channels[0]["active"] is True

    def test_multiple_integrations_are_independent(self, client, buffer_mock):
        first = make_integration(client, name="Buffer A", key="token_aaaaaaaa_1111")
        second = make_integration(client, name="Buffer B", key="token_bbbbbbbb_2222")

        assert first["id"] != second["id"]
        both = client.get("/api/integrations").json()
        assert len(both) == 2
        assert {i["display_name"] for i in both} == {"Buffer A", "Buffer B"}


# --------------------------------------------------------------------------- #
# Channel discovery + sync
# --------------------------------------------------------------------------- #
class TestChannelDiscovery:
    def test_discover_returns_real_buffer_channels(self, client, buffer_mock):
        integration = make_integration(client)

        body = client.post(f"/api/integrations/{integration['id']}/discover").json()

        assert body["total"] == 3
        ids = {c["external_channel_id"] for c in body["channels"]}
        assert ids == {"chan_instagram_1", "chan_linkedin_1", "chan_broken_1"}

        instagram = next(c for c in body["channels"] if c["service"] == "instagram")
        assert instagram["name"] == "glaamora"
        assert instagram["external_link"] == "https://instagram.com/glaamora"
        assert instagram["already_imported"] is False

        # Buffer's own health flags are surfaced, not invented.
        broken = next(c for c in body["channels"] if c["external_channel_id"] == "chan_broken_1")
        assert broken["disconnected"] is True
        assert broken["local_channel_id"] is None

    def test_sync_creates_channels_that_appear_in_selectors(self, client, buffer_mock, brand):
        integration = make_integration(client)

        response = client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={
                "account_id": "sortpi",
                "external_channel_ids": ["chan_instagram_1", "chan_linkedin_1"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["created"] == 2
        assert body["updated"] == 0

        # No code change needed: the new brand's channels are served by the
        # same endpoints the Quick Publisher already calls.
        channels = client.get("/api/accounts/sortpi/channels").json()
        assert [c["platform"] for c in channels] == ["instagram", "linkedin"]
        assert all(c["publishable"] is True for c in channels)
        assert all(c["integration_id"] == integration["id"] for c in channels)

        # The brand that had no integration is untouched: its own pre-existing
        # channels are still served exactly as before.
        seeded = client.get("/api/accounts/company/channels").json()
        assert len(seeded) == 3
        assert all(c["integration_id"] is None for c in seeded)

    def test_sync_is_idempotent_and_never_duplicates(self, client, buffer_mock, brand):
        integration = make_integration(client)
        payload = {
            "account_id": "sortpi",
            "external_channel_ids": ["chan_instagram_1", "chan_linkedin_1"],
        }

        first = client.post(f"/api/integrations/{integration['id']}/sync", json=payload).json()
        second = client.post(f"/api/integrations/{integration['id']}/sync", json=payload).json()
        third = client.post(f"/api/integrations/{integration['id']}/sync", json=payload).json()

        assert first["created"] == 2
        assert second["created"] == 0 and second["updated"] == 2
        assert third["created"] == 0 and third["updated"] == 2

        channels = client.get("/api/accounts/sortpi/channels").json()
        assert len(channels) == 2

    def test_reassignment_to_another_account_is_refused(self, client, buffer_mock, brand):
        integration = make_integration(client)
        client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "sortpi", "external_channel_ids": ["chan_instagram_1"]},
        )

        response = client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "company", "external_channel_ids": ["chan_instagram_1"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CHANNEL_ALREADY_ASSIGNED"

    def test_disconnected_channel_is_stored_but_not_publishable(self, client, buffer_mock, brand):
        integration = make_integration(client)
        client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "sortpi", "external_channel_ids": ["chan_broken_1"]},
        )

        channels = client.get("/api/accounts/sortpi/channels").json()
        assert channels[0]["connection_status"] == "reauth_required"
        assert channels[0]["publishable"] is False

    def test_queue_paused_state_is_recorded(self, client, buffer_mock, brand):
        integration = make_integration(client)
        client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "sortpi", "external_channel_ids": ["chan_linkedin_1"]},
        )
        channels = client.get("/api/accounts/sortpi/channels").json()
        assert channels[0]["queue_paused"] is True

    def test_manual_binding_when_discovery_is_unavailable(self, client, brand):
        """The documented fallback: operator supplies the Buffer channel id."""
        make_integration(client, key="tok_manual_777777")
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "sortpi",
                "external_channel_id": "chan_entered_by_hand",
                "platform": "twitter",
                "display_name": "CEO on X",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["platform"] == "twitter"
        assert body["display_name"] == "CEO on X"

        channels = client.get("/api/accounts/sortpi/channels").json()
        assert len(channels) == 1
        assert channels[0]["display_name"] == "CEO on X"

    def test_channel_can_be_disabled_without_being_deleted(self, client, buffer_mock, brand):
        integration = make_integration(client)
        sync = client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "sortpi", "external_channel_ids": ["chan_instagram_1"]},
        ).json()
        channel_id = sync["channel_ids"][0]

        client.patch(f"/api/channels/{channel_id}", json={"active": False})

        # Gone from the default (active) selector view...
        assert client.get("/api/accounts/sortpi/channels").json() == []
        # ...but still present for administration.
        admin = client.get("/api/accounts/sortpi/channels?include_inactive=true").json()
        assert len(admin) == 1

    def test_channels_that_predate_integrations_are_not_flagged_as_broken(self, client):
        """Existing installs keep publishing through n8n; don't alarm the user.

        These seeded channels have no integration row. The backend cannot know
        whether the n8n workflow holds a mapping for them, so it must not claim
        they cannot publish.
        """
        channels = client.get("/api/accounts/company/channels").json()
        assert len(channels) == 3
        for channel in channels:
            assert channel["integration_id"] is None
            assert channel["connection_status"] == "unknown"
            assert channel["publishable"] is True


class TestLegacyCompatibility:
    def test_seeded_channels_are_untouched_by_the_migration(self, client):
        assert {c["id"] for c in client.get("/api/accounts/company/channels").json()} == {
            "company_instagram",
            "company_facebook",
            "company_linkedin",
        }

    def test_legacy_publish_path_is_still_the_default(self, client):
        """PUBLISH_ENGINE defaults to n8n, so an existing install is unchanged."""
        assert client.get("/api/system/status").json()["publish_engine"] == "n8n"

    def test_legacy_publish_still_reaches_the_n8n_webhook(self, client, fake_n8n):
        """With no integration bound, publishing goes to n8n exactly as before."""
        response = client.post(
            "/api/publish",
            json={
                "account_id": "company",
                "channel_id": "company_linkedin",
                "content": "legacy path still works",
                "publish_mode": "NOW",
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True
        # The original webhook contract is unchanged.
        assert len(fake_n8n.calls) == 1
        payload = fake_n8n.calls[0]
        assert payload["account_id"] == "company"
        assert payload["channel_id"] == "company_linkedin"
        assert payload["content"] == "legacy path still works"
        assert payload["publish_mode"] == "NOW"


# --------------------------------------------------------------------------- #
# Publishing safety
# --------------------------------------------------------------------------- #
class TestPublishingSafety:
    def _bind(self, client, account="sortpi", external="chan_linkedin_1"):
        integration = make_integration(client)
        sync = client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": account, "external_channel_ids": [external]},
        ).json()
        return integration, sync["channel_ids"][0]

    def test_direct_publishing_sends_the_resolved_buffer_channel_id(
        self, client, buffer_mock, direct_engine, brand
    ):
        _, channel_id = self._bind(client)

        response = client.post(
            "/api/publish",
            json={
                "account_id": "sortpi",
                "channel_id": channel_id,
                "content": "Hello from the new engine",
                "publish_mode": "NOW",
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["status"] == "published"
        assert body["buffer_publication_id"] == "post_abc"

        # The mutation carried Buffer's own channel id, resolved server-side.
        create_calls = [c for c in buffer_mock if c and "createPost" in str(c)]
        assert create_calls
        assert create_calls[0]["variables"]["input"]["channelId"] == "chan_linkedin_1"

    def test_disabled_integration_cannot_publish_silently(
        self, client, buffer_mock, direct_engine, brand
    ):
        integration, channel_id = self._bind(client)
        client.patch(f"/api/integrations/{integration['id']}", json={"enabled": False})

        response = client.post(
            "/api/publish",
            json={
                "account_id": "sortpi",
                "channel_id": channel_id,
                "content": "should never go out",
                "publish_mode": "NOW",
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INTEGRATION_DISABLED"

        # Nothing reached Buffer.
        assert not [c for c in buffer_mock if c and "createPost" in str(c)]

    def test_disconnected_channel_cannot_publish_silently(
        self, client, buffer_mock, direct_engine, brand
    ):
        _, channel_id = self._bind(client, external="chan_broken_1")

        response = client.post(
            "/api/publish",
            json={
                "account_id": "sortpi",
                "channel_id": channel_id,
                "content": "should never go out",
                "publish_mode": "NOW",
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CHANNEL_REAUTH_REQUIRED"

    def test_channel_ownership_is_enforced(self, client, buffer_mock, direct_engine, brand):
        _, channel_id = self._bind(client)

        # Same channel, wrong brand.
        response = client.post(
            "/api/publish",
            json={
                "account_id": "company",
                "channel_id": channel_id,
                "content": "cross-account attempt",
                "publish_mode": "NOW",
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CHANNEL_NOT_BELONG_TO_ACCOUNT"

    def test_failed_publish_is_recorded_as_failed(
        self, client, buffer_mock, direct_engine, brand, monkeypatch
    ):
        _, channel_id = self._bind(client)

        from app.services import buffer_client

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode(errors="ignore")
            if "createPost" in body:
                return httpx.Response(429, json={"errors": [{"message": "rate limit"}]})
            return graphql_handler(request)

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            buffer_client.BufferClient,
            "_http",
            lambda self: httpx.Client(transport=transport, timeout=5.0),
        )

        response = client.post(
            "/api/publish",
            json={
                "account_id": "sortpi",
                "channel_id": channel_id,
                "content": "rate limited",
                "publish_mode": "NOW",
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "BUFFER_RATE_LIMIT"

        history = client.get("/api/publish/history").json()
        assert history["items"][0]["status"] == "failed"

    def test_scheduled_publish_sends_due_at_to_buffer(
        self, client, buffer_mock, direct_engine, brand
    ):
        from datetime import datetime, timedelta, timezone as dt_timezone

        _, channel_id = self._bind(client)
        when = datetime.now(dt_timezone.utc) + timedelta(days=2)

        response = client.post(
            "/api/publish",
            json={
                "account_id": "sortpi",
                "channel_id": channel_id,
                "content": "scheduled for later",
                "publish_mode": "SCHEDULE",
                "scheduled_at": when.isoformat(),
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "scheduled"

        create_calls = [c for c in buffer_mock if c and "createPost" in str(c)]
        assert create_calls
        payload = create_calls[0]["variables"]["input"]
        assert payload["mode"] == "customScheduled"
        assert payload["dueAt"]
        # The internal request id travels to Buffer for traceability.
        assert payload["source"].startswith("app:")


# --------------------------------------------------------------------------- #
# Internal endpoint
# --------------------------------------------------------------------------- #
class TestInternalEndpoint:
    def test_absent_secret_means_the_endpoint_does_not_exist(self, client):
        response = client.post(
            "/internal/publish",
            json={
                "request_id": "r1",
                "account_id": "company",
                "channel_id": "x",
                "content": "y",
            },
        )
        assert response.status_code == 404

    def test_wrong_secret_is_rejected(self, client, buffer_mock, internal_secret):
        response = client.post(
            "/internal/publish",
            json={
                "request_id": "r1",
                "account_id": "company",
                "channel_id": "x",
                "content": "y",
            },
            headers={"X-Internal-Secret": "wrong"},
        )
        assert response.status_code == 401

    def test_correct_secret_publishes_and_records_history(
        self, client, buffer_mock, internal_secret, brand
    ):
        integration = make_integration(client)
        sync = client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "sortpi", "external_channel_ids": ["chan_instagram_1"]},
        ).json()

        response = client.post(
            "/internal/publish",
            json={
                "request_id": "req_123",
                "account_id": "sortpi",
                "channel_id": sync["channel_ids"][0],
                "content": "via the generic workflow",
                "publish_mode": "NOW",
            },
            headers={"X-Internal-Secret": "correct-horse"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["request_id"] == "req_123"
        assert body["buffer_publication_id"] == "post_abc"

        history = client.get("/api/publish/history").json()
        assert history["items"][0]["status"] == "published"
        assert history["items"][0]["request_id"] == "req_123"

    def test_internal_endpoint_rejects_unbound_channel(
        self, client, buffer_mock, internal_secret
    ):
        """A seeded channel with no integration must not be publishable."""
        response = client.post(
            "/internal/publish",
            json={
                "request_id": "r2",
                "account_id": "company",
                "channel_id": "company_instagram",
                "content": "no integration behind this",
                "publish_mode": "NOW",
            },
            headers={"X-Internal-Secret": "correct-horse"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CHANNEL_NOT_BOUND"


# --------------------------------------------------------------------------- #
# System status
# --------------------------------------------------------------------------- #
class TestSystemStatus:
    def test_reports_real_state(self, client, buffer_mock, brand):
        empty = client.get("/api/system/status").json()
        assert empty["integrations"]["total"] == 0
        assert empty["counts"]["accounts"] >= 1

        integration = make_integration(client)
        client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={
                "account_id": "sortpi",
                "external_channel_ids": ["chan_instagram_1", "chan_broken_1"],
            },
        )

        body = client.get("/api/system/status").json()
        assert body["integrations"]["total"] == 1
        assert body["integrations"]["connected"] == 1
        assert body["integrations"]["channels_bound"] == 2
        assert body["integrations"]["channels_disconnected"] == 1
        assert body["encryption"]["configured"] is True
        assert body["n8n"]["in_use"] is True

    def test_status_never_leaks_the_token(self, client, buffer_mock):
        make_integration(client, key="super_secret_token_abc")
        raw = client.get("/api/system/status").text
        assert "super_secret_token_abc" not in raw

    def test_direct_engine_flag_is_reported(self, client, direct_engine):
        assert client.get("/api/system/status").json()["publish_engine"] == "direct"

    def test_public_media_url_silences_the_s3_warning(self, client, monkeypatch):
        """A deployment with signed public URLs must not be told to set up S3.

        The warning used to key off `media_storage_backend == "s3"` alone while
        the `publishable` field beside it already accounted for a public base
        URL, so a correctly configured deployment was told its media was
        unusable.
        """
        settings = get_settings()
        monkeypatch.setattr(settings, "media_storage_backend", "local", raising=False)
        monkeypatch.setattr(
            settings,
            "public_media_base_url",
            "https://api.example.com/api/media/public",
            raising=False,
        )

        body = client.get("/api/system/status").json()

        assert body["media"]["backend"] == "local"
        assert body["media"]["publishable"] is True
        media_issues = [i for i in body["issues"] if i["area"] == "media"]
        assert media_issues == [], "public media URL was flagged despite being reachable"

    def test_localhost_media_url_is_still_flagged(self, client, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "media_storage_backend", "local", raising=False)
        monkeypatch.setattr(
            settings, "public_media_base_url", "http://localhost:8000/api/media/public", raising=False
        )

        body = client.get("/api/system/status").json()

        assert body["media"]["publishable"] is False
        assert [i for i in body["issues"] if i["area"] == "media"]

    def test_missing_encryption_key_is_reported_as_an_issue(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "credential_encryption_key", None, raising=False)
        monkeypatch.setattr(get_settings(), "ai_encryption_key", None, raising=False)
        body = client.get("/api/system/status").json()
        assert body["encryption"]["configured"] is False
        assert any(i["area"] == "security" for i in body["issues"])

