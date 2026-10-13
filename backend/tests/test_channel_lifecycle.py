"""Regression tests for C1-C5.

C1  manual channel linking must produce a genuinely bound channel
C2  channel removal must exist, be safe, and never touch Buffer
C3  inactive/archived channels must not leak into read paths
C4  brand deletion must key off protected references, not an unfiltered relation
C5  repeated startup must not resurrect or overwrite anything
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.models.entities import Account, Channel, PublishLog
from app.services import account_service
from app.services.errors import AppError, ErrorCode

ORG_ID = "org_abc123"


def _body(request: httpx.Request) -> dict:
    try:
        return json.loads(request.content.decode("utf-8"))
    except Exception:
        return {}


CHANNELS_PAYLOAD: dict = {
    "data": {
        "channels": [
            {
                "id": "chan_ig_1",
                "name": "brandig",
                "displayName": "Brand IG",
                "descriptor": "Instagram Business",
                "service": "instagram",
                "type": "BUSINESS",
                "avatar": None,
                "externalLink": None,
                "isDisconnected": False,
                "isLocked": False,
                "isQueuePaused": False,
                "timezone": "Asia/Dhaka",
            },
            {
                "id": "chan_fb_1",
                "name": "brandfb",
                "displayName": "Brand FB",
                "descriptor": "Facebook Page",
                "service": "facebook",
                "type": "PAGE",
                "avatar": None,
                "externalLink": None,
                "isDisconnected": False,
                "isLocked": False,
                "isQueuePaused": False,
                "timezone": "Asia/Dhaka",
            },
        ]
    }
}

ACCOUNT_PAYLOAD: dict = {
    "data": {"account": {"id": "a1", "organizations": [{"id": ORG_ID, "name": "Main"}]}}
}


@pytest.fixture()
def buffer_mock(monkeypatch):
    from app.services import buffer_client

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        calls.append(body)
        if "organizations" in body.get("query", ""):
            return httpx.Response(200, json=ACCOUNT_PAYLOAD)
        if "channels(input" in body.get("query", ""):
            return httpx.Response(200, json=CHANNELS_PAYLOAD)
        return httpx.Response(400, json={"errors": [{"message": "unexpected"}]})

    transport = httpx.MockTransport(handler)
    original = buffer_client.BufferClient._http

    def patched(self):
        client = httpx.Client(transport=transport, timeout=5.0)
        object.__setattr__(self, "_client", client)
        return original(self)

    monkeypatch.setattr(buffer_client.BufferClient, "_http", patched)
    return calls


@pytest.fixture()
def brand(db):
    account = Account(id="brand_a", name="Brand A", type="brand", active=True, sort_order=1)
    db.add(account)
    db.commit()
    return account


@pytest.fixture()
def second_brand(db):
    account = Account(id="brand_b", name="Brand B", type="brand", active=True, sort_order=2)
    db.add(account)
    db.commit()
    return account


@pytest.fixture()
def empty_workspace(db):
    """Remove the seeded demo brand so global list assertions are meaningful."""
    db.query(Channel).delete()
    db.query(Account).delete()
    db.commit()
    return db


def make_integration(client, name="Buffer", key="tok_test_123456", organization_id=None):
    payload = {"display_name": name, "api_key": key}
    if organization_id:
        payload["organization_id"] = organization_id
    response = client.post("/api/integrations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def import_channel(client, account_id, integration_id, external="chan_ig_1"):
    response = client.post(
        f"/api/integrations/{integration_id}/sync",
        json={"account_id": account_id, "external_channel_ids": [external]},
    )
    assert response.status_code == 200, response.text
    return response.json()["channel_ids"][0]


# --------------------------------------------------------------------------- #
# C1 - manual binding
# --------------------------------------------------------------------------- #
class TestC1ManualBinding:
    def test_valid_binding_creates_a_fully_associated_channel(self, client, buffer_mock, brand):
        integration = make_integration(client)
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_a",
                "integration_id": integration["id"],
                "external_channel_id": "chan_ig_1",
                "platform": "instagram",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["integration_id"] == integration["id"]
        assert body["platform"] == "instagram"
        # The decisive assertion: it is genuinely publishable, not merely
        # present in a selector.
        assert body["publishable"] is True, body
        assert body["publish_blocked_reason"] is None

    def test_invalid_integration_id_is_rejected(self, client, buffer_mock, brand):
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_a",
                "integration_id": "int_does_not_exist",
                "external_channel_id": "chan_ig_1",
                "platform": "instagram",
            },
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "INTEGRATION_NOT_FOUND"

    def test_unknown_external_channel_is_rejected(self, client, buffer_mock, brand):
        integration = make_integration(client)
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_a",
                "integration_id": integration["id"],
                "external_channel_id": "chan_typo_in_id",
                "platform": "instagram",
            },
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CHANNEL_NOT_IN_INTEGRATION"
        # Nothing was written: a rejected binding leaves no partial row.
        assert client.get("/api/accounts/brand_a/channels").json() == []

    def test_platform_mismatch_is_rejected(self, client, buffer_mock, brand):
        """Buffer is the authority on which platform a profile belongs to."""
        integration = make_integration(client)
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_a",
                "integration_id": integration["id"],
                "external_channel_id": "chan_ig_1",
                "platform": "facebook",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "PLATFORM_NOT_SUPPORTED"

    def test_unsupported_platform_is_rejected(self, client, buffer_mock, brand):
        integration = make_integration(client)
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_a",
                "integration_id": integration["id"],
                "external_channel_id": "chan_ig_1",
                "platform": "myspace",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "PLATFORM_NOT_SUPPORTED"

    def test_binding_to_an_inactive_brand_is_rejected(self, client, buffer_mock, db, brand):
        integration = make_integration(client)
        brand.active = False
        db.commit()
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_a",
                "integration_id": integration["id"],
                "external_channel_id": "chan_ig_1",
                "platform": "instagram",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ACCOUNT_INACTIVE"

    def test_binding_to_a_disabled_integration_is_rejected(self, client, buffer_mock, brand):
        integration = make_integration(client)
        client.patch(f"/api/integrations/{integration['id']}", json={"enabled": False})
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_a",
                "integration_id": integration["id"],
                "external_channel_id": "chan_ig_1",
                "platform": "instagram",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INTEGRATION_DISABLED"

    def test_a_channel_cannot_be_claimed_by_two_brands(self, client, buffer_mock, brand, second_brand):
        """H2: one Buffer profile, one owning brand, enforced."""
        integration = make_integration(client)
        client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_a",
                "integration_id": integration["id"],
                "external_channel_id": "chan_ig_1",
                "platform": "instagram",
            },
        )
        response = client.post(
            "/api/channels/bind",
            json={
                "account_id": "brand_b",
                "integration_id": integration["id"],
                "external_channel_id": "chan_ig_1",
                "platform": "instagram",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CHANNEL_ALREADY_ASSIGNED"


# --------------------------------------------------------------------------- #
# C2 - removal is archival, and never touches Buffer
# --------------------------------------------------------------------------- #
class TestC2ChannelRemoval:
    def test_delete_archives_and_hides_the_channel(self, client, buffer_mock, empty_workspace, brand):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])

        response = client.delete(f"/api/channels/{channel_id}")
        assert response.status_code == 204

        # Gone from the ordinary views...
        assert client.get("/api/accounts/brand_a/channels").json() == []
        assert client.get("/api/channels").json() == []

        # ...but the row survives for the audit trail.
        admin = client.get(
            "/api/accounts/brand_a/channels", params={"include_inactive": True}
        ).json()
        assert len(admin) == 1
        assert admin[0]["archived_at"] is not None
        assert admin[0]["publishable"] is False

    def test_delete_releases_the_external_assignment(
        self, client, buffer_mock, empty_workspace, brand, second_brand
    ):
        """After archiving, another brand can import the same Buffer profile."""
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])

        assert client.delete(f"/api/channels/{channel_id}").status_code == 204

        # The released id no longer blocks a different brand.
        response = client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "brand_b", "external_channel_ids": ["chan_ig_1"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1
        assert len(client.get("/api/accounts/brand_b/channels").json()) == 1

    def test_delete_is_idempotent(self, client, buffer_mock, brand):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        assert client.delete(f"/api/channels/{channel_id}").status_code == 204
        assert client.delete(f"/api/channels/{channel_id}").status_code == 204

    def test_delete_of_an_unknown_channel_is_404(self, client, buffer_mock):
        assert client.delete("/api/channels/ch_nope").status_code == 404

    def test_delete_preserves_publishing_history(self, client, buffer_mock, empty_workspace, brand, db):
        """Audit history must survive the removal of the channel."""
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        db.add(
            PublishLog(
                request_id="req_keepme",
                account_id="brand_a",
                channel_id=channel_id,
                platform="instagram",
                content="already published",
                content_preview="already published",
                publish_mode="NOW",
                status="published",
            )
        )
        db.commit()

        assert client.delete(f"/api/channels/{channel_id}").status_code == 204

        history = client.get("/api/publish/history", params={"channel_id": channel_id}).json()
        assert history["total"] == 1
        assert history["items"][0]["content_preview"] == "already published"

    def test_delete_sends_nothing_to_buffer(self, client, buffer_mock, brand):
        """Only discovery/workspace queries are ever issued."""
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        before = len(buffer_mock)

        assert client.delete(f"/api/channels/{channel_id}").status_code == 204

        # No outbound call at all during archival.
        assert len(buffer_mock) == before

    def test_archived_channel_cannot_be_published_to(self, client, buffer_mock, brand, fake_n8n):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        client.delete(f"/api/channels/{channel_id}")

        response = client.post(
            "/api/publish",
            json={
                "account_id": "brand_a",
                "channel_id": channel_id,
                "content": "must not go out",
                "publish_mode": "NOW",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CHANNEL_ARCHIVED"
        assert fake_n8n.calls == []

    def test_restore_clears_the_archive_but_does_not_fake_a_binding(self, client, buffer_mock, brand):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        client.delete(f"/api/channels/{channel_id}")

        response = client.post(f"/api/channels/{channel_id}/restore")
        assert response.status_code == 200
        body = response.json()
        assert body["archived_at"] is None
        # Honest: it is not linked again until it is re-imported.
        assert body["publishable"] is False
        assert body["publish_blocked_code"] == "CHANNEL_NOT_BOUND"

    def test_reassign_moves_a_channel_to_another_brand(self, client, buffer_mock, brand, second_brand):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])

        response = client.post(
            f"/api/channels/{channel_id}/reassign", json={"account_id": "brand_b"}
        )
        assert response.status_code == 200
        assert response.json()["account_id"] == "brand_b"
        assert client.get("/api/accounts/brand_a/channels").json() == []
        assert len(client.get("/api/accounts/brand_b/channels").json()) == 1


# --------------------------------------------------------------------------- #
# C3 - inactive channels must not leak
# --------------------------------------------------------------------------- #
class TestC3InactiveFiltering:
    def test_inactive_channel_is_hidden_from_selectors_but_visible_to_admin(
        self, client, buffer_mock, empty_workspace, brand
    ):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])

        client.patch(f"/api/channels/{channel_id}", json={"active": False})

        assert client.get("/api/accounts/brand_a/channels").json() == []
        assert client.get("/api/channels").json() == []
        admin = client.get("/api/channels", params={"include_inactive": True}).json()
        assert len(admin) == 1

    def test_account_detail_hides_inactive_channels(self, client, buffer_mock, brand):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        client.patch(f"/api/channels/{channel_id}", json={"active": False})

        assert client.get("/api/accounts/brand_a").json()["channels"] == []
        admin = client.get(
            "/api/accounts/brand_a", params={"include_inactive": True}
        ).json()
        assert len(admin["channels"]) == 1

    def test_channels_of_a_disabled_brand_are_hidden_by_default(self, client, buffer_mock, empty_workspace, brand, db):
        integration = make_integration(client)
        import_channel(client, "brand_a", integration["id"])
        brand.active = False
        db.commit()

        assert client.get("/api/channels").json() == []
        admin = client.get(
            "/api/channels", params={"include_inactive_accounts": True}
        ).json()
        assert len(admin) == 1

    def test_channel_count_reflects_only_live_channels(self, client, buffer_mock, empty_workspace, brand):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        assert client.get("/api/accounts").json()[0]["channel_count"] == 1

        client.patch(f"/api/channels/{channel_id}", json={"active": False})
        assert client.get("/api/accounts/manage").json()[0]["channel_count"] == 0

    def test_dashboard_counts_split_active_from_archived(self, client, buffer_mock, empty_workspace, brand):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        client.delete(f"/api/channels/{channel_id}")

        counts = client.get("/api/system/status").json()["counts"]
        assert counts["channels"] == 1
        assert counts["channels_active"] == 0
        assert counts["channels_archived"] == 1

    def test_history_is_not_hidden_when_a_channel_is_archived(self, client, buffer_mock, brand, db):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        db.add(
            PublishLog(
                request_id="req_visible",
                account_id="brand_a",
                channel_id=channel_id,
                platform="instagram",
                content="went out already",
                publish_mode="NOW",
                status="published",
            )
        )
        db.commit()
        client.delete(f"/api/channels/{channel_id}")

        assert client.get("/api/publish/history").json()["total"] == 1


# --------------------------------------------------------------------------- #
# C4 - brand deletion
# --------------------------------------------------------------------------- #
class TestC4BrandDeletion:
    def test_brand_with_channels_cannot_be_deleted(self, client, buffer_mock, brand):
        integration = make_integration(client)
        import_channel(client, "brand_a", integration["id"])

        response = client.delete("/api/accounts/brand_a")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ACCOUNT_STILL_HAS_CHANNELS"

    def test_brand_with_history_is_never_hard_deleted(self, client, buffer_mock, brand, db):
        """History is the reason this rule exists."""
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        db.add(
            PublishLog(
                request_id="req_hist",
                account_id="brand_a",
                channel_id=channel_id,
                platform="instagram",
                content="already out there",
                publish_mode="NOW",
                status="published",
            )
        )
        db.commit()
        client.delete(f"/api/channels/{channel_id}")

        response = client.delete("/api/accounts/brand_a")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ACCOUNT_HAS_HISTORY"
        # The brand and the history both survive.
        assert client.get("/api/accounts/brand_a").status_code == 200
        assert client.get("/api/publish/history").json()["total"] == 1

    def test_empty_brand_can_be_deleted(self, client, buffer_mock, brand):
        assert client.delete("/api/accounts/brand_a").status_code == 204
        assert client.get("/api/accounts/brand_a").status_code == 404

    def test_archiving_every_channel_alone_does_not_unlock_deletion(
        self, client, buffer_mock, brand
    ):
        """Archived rows are still referenced, so the brand stays."""
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        client.delete(f"/api/channels/{channel_id}")

        assert client.delete("/api/accounts/brand_a").status_code == 409

    def test_deactivating_a_brand_is_the_safe_alternative(self, client, buffer_mock, empty_workspace, brand):
        integration = make_integration(client)
        import_channel(client, "brand_a", integration["id"])

        response = client.patch("/api/accounts/brand_a", json={"active": False})
        assert response.status_code == 200
        # Hidden from the publishing selectors, history intact.
        assert [a["id"] for a in client.get("/api/accounts").json()] == []
        assert client.get("/api/publish/history").status_code == 200

    def test_protected_reference_counts_are_exposed(self, client, buffer_mock, brand, db):
        integration = make_integration(client)
        channel_id = import_channel(client, "brand_a", integration["id"])
        refs = account_service.account_protected_references(db, "brand_a")
        assert refs == {
            "active_channels": 1,
            "archived_channels": 0,
            "publish_logs": 0,
            "campaign_posts": 0,
        }

        client.delete(f"/api/channels/{channel_id}")
        refs = account_service.account_protected_references(db, "brand_a")
        assert refs["active_channels"] == 0
        assert refs["archived_channels"] == 1


# --------------------------------------------------------------------------- #
# C5 - seeding
# --------------------------------------------------------------------------- #
class TestC5Seeding:
    def test_seed_is_a_no_op_when_accounts_exist(self, db):
        from app.database.seed import database_is_empty, seed

        assert not database_is_empty(db)
        assert seed(db) == 0

    def test_repeated_startup_does_not_resurrect_a_removed_channel(self, client, db):
        """The row must stay archived, and stay out of the selectors, forever."""
        from app.database.seed import seed

        seeded = client.get("/api/accounts/company/channels").json()
        assert seeded, "the demo brand should exist on first boot"
        channel_id = seeded[0]["id"]
        assert client.delete(f"/api/channels/{channel_id}").status_code == 204

        # Simulate several restarts.
        for _ in range(3):
            seed(db)

        # Not selectable...
        visible = client.get("/api/accounts/company/channels").json()
        assert channel_id not in {c["id"] for c in visible}
        # ...and still archived, not resurrected as active.
        admin = client.get(
            "/api/accounts/company/channels", params={"include_inactive": True}
        ).json()
        row = next(c for c in admin if c["id"] == channel_id)
        assert row["archived_at"] is not None
        assert row["active"] is False

    def test_repeated_startup_does_not_overwrite_brand_edits(self, client, db):
        from app.database.seed import seed

        client.patch(
            "/api/accounts/company",
            json={"name": "Geekssort", "description": "our real brand", "color": "#123456"},
        )

        for _ in range(3):
            seed(db)

        body = client.get("/api/accounts/company").json()
        assert body["name"] == "Geekssort"
        assert body["description"] == "our real brand"
        assert body["color"] == "#123456"

    def test_seed_runs_only_on_an_empty_database(self, db):
        from app.database.seed import seed

        db.query(Account).delete()
        db.query(Channel).delete()
        db.commit()

        assert seed(db) == 4
        # Second call must not duplicate anything.
        assert seed(db) == 0

    def test_production_never_seeds_demo_data(self, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "seed_demo_data", True, raising=False)
        monkeypatch.setattr(settings, "app_env", "production", raising=False)
        assert settings.effective_seed_demo_data is False

    def test_development_may_seed(self, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "seed_demo_data", True, raising=False)
        monkeypatch.setattr(settings, "app_env", "development", raising=False)
        assert settings.effective_seed_demo_data is True

    def test_lifespan_startup_is_idempotent(self):
        """Boot the app twice against the same database."""
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app):
            pass
        with TestClient(app):
            pass

        from app.database.session import SessionLocal

        with SessionLocal() as db:
            assert db.query(Account).count() == 1
            assert db.query(Channel).count() == 3


# --------------------------------------------------------------------------- #
# C9 - testing a connection must not move the workspace
# --------------------------------------------------------------------------- #
MULTI_ORG_PAYLOAD: dict = {
    "data": {
        "account": {
            "id": "a1",
            "organizations": [
                {"id": "org_one", "name": "Agency"},
                {"id": "org_two", "name": "Client"},
            ],
        }
    }
}


class TestC9WorkspacePreservation:
    @pytest.fixture()
    def multi_org(self, monkeypatch):
        from app.services import buffer_client

        def handler(request: httpx.Request) -> httpx.Response:
            body = _body(request)
            if "organizations" in body.get("query", ""):
                return httpx.Response(200, json=MULTI_ORG_PAYLOAD)
            if "channels(input" in body.get("query", ""):
                return httpx.Response(200, json=CHANNELS_PAYLOAD)
            return httpx.Response(400, json={"errors": [{"message": "unexpected"}]})

        transport = httpx.MockTransport(handler)
        original = buffer_client.BufferClient._http

        def patched(self):
            client = httpx.Client(transport=transport, timeout=5.0)
            object.__setattr__(self, "_client", client)
            return original(self)

        monkeypatch.setattr(buffer_client.BufferClient, "_http", patched)

    def test_several_workspaces_require_an_explicit_choice(self, client, multi_org, brand):
        integration = client.post(
            "/api/integrations", json={"display_name": "Multi", "api_key": "tok_multi"}
        ).json()
        assert integration["status"] == "not_configured"
        assert integration["external_organization_id"] is None
        assert len(integration["available_organizations"]) == 2

    def test_testing_never_changes_the_selected_workspace(self, client, multi_org, brand):
        integration = client.post(
            "/api/integrations",
            json={
                "display_name": "Multi",
                "api_key": "tok_multi",
                "organization_id": "org_two",
            },
        ).json()
        assert integration["external_organization_id"] == "org_two"

        for _ in range(3):
            result = client.post(f"/api/integrations/{integration['id']}/test").json()
            assert result["ok"] is True
            assert result["organization_id"] == "org_two"

        current = client.get(f"/api/integrations/{integration['id']}").json()
        assert current["external_organization_id"] == "org_two"

    def test_rotating_the_token_preserves_the_workspace(self, client, multi_org, brand):
        integration = client.post(
            "/api/integrations",
            json={
                "display_name": "Multi",
                "api_key": "tok_multi",
                "organization_id": "org_one",
            },
        ).json()

        rotated = client.post(
            f"/api/integrations/{integration['id']}/rotate", json={"api_key": "tok_multi_2"}
        ).json()
        assert rotated["external_organization_id"] == "org_one"

    def test_workspace_can_be_chosen_explicitly(self, client, multi_org, brand):
        integration = client.post(
            "/api/integrations", json={"display_name": "Multi", "api_key": "tok_multi"}
        ).json()

        response = client.patch(
            f"/api/integrations/{integration['id']}", json={"organization_id": "org_two"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["external_organization_id"] == "org_two"
        assert body["status"] == "connected"

    def test_an_unknown_workspace_is_refused_not_silently_swapped(self, client, multi_org, brand):
        integration = client.post(
            "/api/integrations", json={"display_name": "Multi", "api_key": "tok_multi"}
        ).json()
        response = client.patch(
            f"/api/integrations/{integration['id']}", json={"organization_id": "org_nope"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "BUFFER_ORGANIZATION_NOT_FOUND"

    def test_a_workspace_that_vanished_is_reported(self, client, multi_org, brand):
        """Losing access to a bound workspace must not silently rebind."""
        integration = client.post(
            "/api/integrations",
            json={
                "display_name": "Multi",
                "api_key": "tok_multi",
                "organization_id": "org_two",
            },
        ).json()

        # The token now only sees a different workspace.
        MULTI_ORG_PAYLOAD["data"]["account"]["organizations"] = [
            {"id": "org_three", "name": "Other"}
        ]
        try:
            result = client.post(f"/api/integrations/{integration['id']}/test").json()
            assert result["ok"] is False
            assert result["status"] == "sync_required"
            current = client.get(f"/api/integrations/{integration['id']}").json()
            # The old selection is still recorded, not quietly replaced.
            assert current["external_organization_id"] == "org_two"
        finally:
            MULTI_ORG_PAYLOAD["data"]["account"]["organizations"] = [
                {"id": "org_one", "name": "Agency"},
                {"id": "org_two", "name": "Client"},
            ]


# --------------------------------------------------------------------------- #
# Archive timestamps and integrity
# --------------------------------------------------------------------------- #
def test_archived_at_is_recorded_with_a_reason(client, buffer_mock, brand):
    integration = make_integration(client)
    channel_id = import_channel(client, "brand_a", integration["id"])

    client.delete(f"/api/channels/{channel_id}", params={"reason": "wrong_brand"})
    admin = client.get(
        "/api/accounts/brand_a/channels", params={"include_inactive": True}
    ).json()
    archived_at = admin[0]["archived_at"]
    assert archived_at is not None
    datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
    assert admin[0]["archived_reason"] == "wrong_brand"


def test_an_archived_channel_cannot_be_reactivated_through_patch(client, buffer_mock, brand):
    integration = make_integration(client)
    channel_id = import_channel(client, "brand_a", integration["id"])
    client.delete(f"/api/channels/{channel_id}")

    response = client.patch(f"/api/channels/{channel_id}", json={"active": True})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CHANNEL_ARCHIVED"


def test_eligibility_reports_a_reason_for_every_blocked_state(client, buffer_mock, brand):
    """H3/H4: no channel is silently greyed out without an explanation."""
    from app.database.session import SessionLocal
    from app.models.entities import Channel as ChannelModel

    integration = make_integration(client)
    channel_id = import_channel(client, "brand_a", integration["id"])

    with SessionLocal() as db:
        assert account_service.eligibility(db.get(ChannelModel, channel_id)).publishable is True

        row = db.get(ChannelModel, channel_id)
        row.is_locked = True
        db.commit()
        verdict = account_service.eligibility(row)
        assert verdict.publishable is False
        assert verdict.reason_code == ErrorCode.CHANNEL_LOCKED
        assert "locked" in verdict.reason.lower()

        row.is_locked = False
        row.queue_paused = True
        db.commit()
        verdict = account_service.eligibility(row)
        assert verdict.publishable is True
        assert verdict.reason_code == "QUEUE_PAUSED"

        row.queue_paused = False
        row.connection_status = "reauth_required"
        db.commit()
        verdict = account_service.eligibility(row)
        assert verdict.publishable is False
        assert verdict.reason_code == ErrorCode.CHANNEL_REAUTH_REQUIRED

def test_channel_responses_carry_the_owning_brand_name(
    client, buffer_mock, brand, second_brand
):
    """A picker spanning brands must be able to name the brand it belongs to.

    ``ChannelDetail.account_name`` was declared on the response schema but never
    populated, so every cross-brand picker showed bare profile names and an
    operator could not tell which brand a channel belonged to before assigning
    it. Guarded on both the per-brand and the global endpoint.
    """
    integration = make_integration(client)
    a_id = import_channel(client, "brand_a", integration["id"], external="chan_ig_1")
    b_id = import_channel(client, "brand_b", integration["id"], external="chan_fb_1")

    one = client.get(f"/api/accounts/brand_a/channels").json()
    assert [c["account_name"] for c in one] == ["Brand A"]

    every = client.get("/api/channels").json()
    by_id = {c["id"]: c for c in every}
    assert by_id[a_id]["account_name"] == "Brand A"
    assert by_id[b_id]["account_name"] == "Brand B"

    # The id must stay the authoritative link; the name is display only.
    assert by_id[a_id]["account_id"] == "brand_a"
    assert by_id[b_id]["account_id"] == "brand_b"


def test_a_channel_can_be_moved_between_brands_and_keeps_its_binding(
    client, buffer_mock, brand, second_brand
):
    """Any channel must be assignable to any brand without losing its Buffer link.

    Moving must not unbind the channel, drop its external id, or disturb the
    other brand's channels.
    """
    integration = make_integration(client)
    a_id = import_channel(client, "brand_a", integration["id"], external="chan_ig_1")
    b_id = import_channel(client, "brand_b", integration["id"], external="chan_fb_1")

    moved = client.post(f"/api/channels/{a_id}/reassign", json={"account_id": "brand_b"})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["account_id"] == "brand_b"
    assert body["account_name"] == "Brand B"
    assert body["integration_id"] == integration["id"]
    assert body["publishable"] is True

    # The external Buffer id is deliberately never serialised to the browser,
    # so the binding is asserted against the database instead.
    from app.database.session import SessionLocal
    from app.models.entities import Channel as ChannelModel

    with SessionLocal() as db:
        row = db.get(ChannelModel, a_id)
        assert row.external_channel_id == "chan_ig_1"
        assert row.account_id == "brand_b"

    # It now appears under the new brand and is gone from the old one.
    assert [c["id"] for c in client.get("/api/accounts/brand_a/channels").json()] == []
    assert sorted(c["id"] for c in client.get("/api/accounts/brand_b/channels").json()) == sorted(
        [a_id, b_id]
    )

    # Moving it back works the same way.
    back = client.post(f"/api/channels/{a_id}/reassign", json={"account_id": "brand_a"})
    assert back.status_code == 200, back.text
    assert back.json()["account_name"] == "Brand A"
    with SessionLocal() as db:
        assert db.get(ChannelModel, a_id).external_channel_id == "chan_ig_1"
