"""Regression tests for C7 and C8.

C7  one logical publishing attempt must produce exactly one history row
C8  a failed attempt must be retryable; a completed one must never repeat
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.entities import IdempotencyRecord, PublishLog
from tests.conftest import FakeN8n

FUTURE = timedelta(days=1)


def _future() -> str:
    tz = timezone(timedelta(hours=6))
    return (datetime.now(tz) + FUTURE).strftime("%Y-%m-%dT%H:%M:%S")


def _payload(**overrides) -> dict:
    base = {
        "account_id": "company",
        "channel_id": "company_facebook",
        "content": "History dedup check",
        "media": [],
        "publish_mode": "NOW",
        "scheduled_at": None,
        "timezone": "Asia/Dhaka",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# C7 - one attempt, one row
# --------------------------------------------------------------------------- #
class TestC7SingleHistoryRow:
    def test_a_successful_publish_writes_exactly_one_row(
        self, client, fake_n8n, bound_company_channels
    ):
        client.post("/api/publish", json=_payload())
        history = client.get("/api/publish/history").json()
        assert history["total"] == 1
        assert history["items"][0]["status"] == "published"

    def test_the_callback_updates_the_same_row_rather_than_adding_one(
        self, client, fake_n8n, db, monkeypatch, allow_publish
    ):
        """The full n8n round trip: publish, then the callback confirms.

        This is the exact scenario that produced two rows: the browser-facing
        route wrote one and the workflow callback wrote another. The transport is
        mocked inside the test, so nothing reaches Buffer.
        """
        import json

        import httpx

        from app.core.config import get_settings
        from app.services import buffer_client

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            query = body.get("query", "")
            if "organizations" in query:
                return httpx.Response(
                    200,
                    json={"data": {"account": {"id": "a", "organizations": [
                        {"id": "org_c7", "name": "W"}]}}},
                )
            if "channels(input" in query:
                return httpx.Response(
                    200,
                    json={"data": {"channels": [{
                        "id": "chan_fb_1", "name": "company", "displayName": "Company FB",
                        "descriptor": "Facebook Page", "service": "facebook", "type": "PAGE",
                        "avatar": None, "externalLink": None, "isDisconnected": False,
                        "isLocked": False, "isQueuePaused": False, "timezone": "Asia/Dhaka",
                    }]}},
                )
            if "createPost" in query:
                return httpx.Response(
                    200,
                    json={"data": {"createPost": {"__typename": "PostActionSuccess", "post": {
                        "id": "post_c7", "status": "published",
                        "externalLink": "https://example/c7",
                    }}}},
                )
            return httpx.Response(400, json={"errors": [{"message": "no"}]})

        # Installed before anything else so even integration setup is mocked.
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            buffer_client.BufferClient,
            "_http",
            lambda self: httpx.Client(transport=transport, timeout=5.0),
        )

        integration = client.post(
            "/api/integrations", json={"display_name": "B", "api_key": "tok_c7_1"}
        ).json()
        assert integration["status"] == "connected", integration
        sync = client.post(
            f"/api/integrations/{integration['id']}/sync",
            json={"account_id": "company", "external_channel_ids": ["chan_fb_1"]},
        ).json()
        channel_id = sync["channel_ids"][0]

        published = client.post(
            "/api/publish",
            json=_payload(channel_id=channel_id),
            headers={"X-Request-Id": "req_c7_shared"},
        )
        assert published.status_code == 200, published.text

        monkeypatch.setattr(
            get_settings(), "internal_service_secret", "s3cret", raising=False
        )

        callback = client.post(
            "/internal/publish",
            json={
                "request_id": "req_c7_shared",
                "account_id": "company",
                "channel_id": channel_id,
                "content": "History dedup check",
                "publish_mode": "NOW",
            },
            headers={"X-Internal-Secret": "s3cret"},
        )
        assert callback.status_code == 200, callback.text

        history = client.get("/api/publish/history").json()
        assert history["total"] == 1, "the callback created a duplicate row"
        row = history["items"][0]
        assert row["request_id"] == "req_c7_shared"
        assert row["buffer_publication_id"] == "post_c7"
        assert row["content_preview"] == "History dedup check"

    def test_record_publish_attempt_is_idempotent_on_request_id(self, db):
        from app.services.publish_history import record_publish_attempt

        kwargs = dict(
            request_id="req_dup", account_id="company", channel_id="company_facebook",
            platform="facebook", content="x", media_count=0, publish_mode="NOW",
            scheduled_at=None, timezone_name="Asia/Dhaka",
        )
        first = record_publish_attempt(db, **kwargs)
        second = record_publish_attempt(db, **kwargs)
        assert first.id == second.id
        assert db.query(PublishLog).filter(PublishLog.request_id == "req_dup").count() == 1

    def test_a_confirmed_outcome_updates_rather_than_inserts(self, db, client):
        from app.services.publish_history import confirm_publish_attempt, record_publish_attempt

        row = record_publish_attempt(
            db, request_id="req_upd", account_id="company",
            channel_id="company_facebook", platform="facebook", content="x",
            media_count=0, publish_mode="NOW", scheduled_at=None, timezone_name="UTC",
        )
        confirm_publish_attempt(
            db, request_id="req_upd", status="published",
            buffer_publication_id="post_upd", account_id="company",
            channel_id="company_facebook",
        )
        db.refresh(row)
        assert row.status == "published"
        assert row.buffer_publication_id == "post_upd"
        assert db.query(PublishLog).filter(PublishLog.request_id == "req_upd").count() == 1

    def test_history_count_is_not_doubled(self, client, fake_n8n, bound_company_channels):
        for i in range(5):
            client.post("/api/publish", json=_payload(content=f"post {i}"))
        history = client.get("/api/publish/history").json()
        assert history["total"] == 5


# --------------------------------------------------------------------------- #
# C8 - retry policy
# --------------------------------------------------------------------------- #
class TestC8IdempotencyAndRetry:
    def test_a_completed_key_replays_instead_of_publishing_again(
        self, client, fake_n8n, bound_company_channels
    ):
        headers = {"Idempotency-Key": "retry-1"}
        first = client.post("/api/publish", json=_payload(), headers=headers)
        assert first.status_code == 200

        second = client.post("/api/publish", json=_payload(), headers=headers)
        assert second.status_code == 200
        assert second.json()["idempotent_replay"] is True
        # The engine saw exactly one call.
        assert len(fake_n8n.calls) == 1

    def test_a_failed_attempt_can_be_retried(
        self, client, fake_n8n, bound_company_channels
    ):
        """The bug: a failure wedged the deterministic key forever."""
        headers = {"Idempotency-Key": "retry-after-failure"}
        fake_n8n.fail("BUFFER_SERVER_ERROR", "transient")

        first = client.post("/api/publish", json=_payload(), headers=headers)
        assert first.status_code == 502, first.text
        assert first.json()["error"]["code"] == "BUFFER_SERVER_ERROR"

        # The provider recovers.
        fake_n8n.succeed_now()

        second = client.post("/api/publish", json=_payload(), headers=headers)
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "published"
        assert len(fake_n8n.calls) == 2

    def test_a_retry_may_carry_corrected_content(
        self, client, fake_n8n, bound_company_channels
    ):
        """A retry exists precisely because the first attempt was wrong."""
        headers = {"Idempotency-Key": "retry-with-fix"}
        fake_n8n.fail("BUFFER_SERVER_ERROR")
        assert client.post("/api/publish", json=_payload(), headers=headers).status_code == 502

        fake_n8n.succeed_now()
        second = client.post(
            "/api/publish", json=_payload(content="corrected copy"), headers=headers
        )
        assert second.status_code == 200, second.text

    def test_retries_are_counted(self, client, fake_n8n, bound_company_channels, db):
        headers = {"Idempotency-Key": "retry-counted"}
        fake_n8n.fail("BUFFER_SERVER_ERROR")
        client.post("/api/publish", json=_payload(), headers=headers)
        fake_n8n.succeed_now()
        client.post("/api/publish", json=_payload(), headers=headers)

        record = db.get(IdempotencyRecord, "retry-counted")
        assert record.state == "completed"
        assert record.attempts == 2

    def test_a_completed_key_with_different_content_is_refused(
        self, client, fake_n8n, bound_company_channels
    ):
        """A key must never be reused to silently publish something else."""
        headers = {"Idempotency-Key": "no-reuse"}
        assert client.post("/api/publish", json=_payload(), headers=headers).status_code == 200

        second = client.post(
            "/api/publish", json=_payload(content="a different post"), headers=headers
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        assert len(fake_n8n.calls) == 1

    def test_a_concurrent_in_flight_key_is_refused(
        self, client, fake_n8n, bound_company_channels, db
    ):
        """The double-submit guard must survive the retry change."""
        db.add(
            IdempotencyRecord(
                key="in-flight",
                request_fingerprint="deadbeef",
                state="in_progress",
                attempts=1,
            )
        )
        db.commit()

        response = client.post(
            "/api/publish", json=_payload(), headers={"Idempotency-Key": "in-flight"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "DUPLICATE_SUBMISSION"
        assert fake_n8n.calls == []

    def test_a_stale_in_flight_key_is_taken_over(self, client, fake_n8n, bound_company_channels, db):
        """A crashed process must not block a key forever."""
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(
            IdempotencyRecord(
                key="stale",
                request_fingerprint="deadbeef",
                state="in_progress",
                attempts=1,
            )
        )
        db.commit()
        record = db.get(IdempotencyRecord, "stale")
        record.updated_at = old
        db.commit()

        response = client.post(
            "/api/publish", json=_payload(), headers={"Idempotency-Key": "stale"}
        )
        assert response.status_code == 200, response.text
        assert len(fake_n8n.calls) == 1

    def test_schedule_and_now_have_separate_keys(self, client, fake_n8n, bound_company_channels):
        """Switching mode must not be blocked by the other mode's key."""
        now = client.post(
            "/api/publish", json=_payload(), headers={"Idempotency-Key": "mode-key"}
        )
        assert now.status_code == 200

        fake_n8n.succeed_scheduled()
        sched = client.post(
            "/api/publish",
            json=_payload(publish_mode="SCHEDULE", scheduled_at=_future()),
            headers={"Idempotency-Key": "mode-key-sched"},
        )
        assert sched.status_code == 200, sched.text

    def test_a_failed_campaign_post_can_be_republished(
        self, client, fake_adapter, provider_row, fake_n8n, bound_company_channels
    ):
        """End to end: the campaign UI's deterministic key must recover."""
        from tests.conftest import make_campaign, wait_for_generation

        campaign = make_campaign(
            client, [{"account_id": "company", "channel_id": "company_facebook"}]
        )
        client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
        wait_for_generation()
        detail = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
        post_id = detail["posts"][0]["id"]

        fake_n8n.fail("BUFFER_SERVER_ERROR")
        first = client.post(f"/api/ai/campaign-posts/{post_id}/publish", json={})
        assert first.json()["ok"] is False
        assert first.json()["error_code"] == "BUFFER_SERVER_ERROR"

        fake_n8n.succeed_now()
        second = client.post(f"/api/ai/campaign-posts/{post_id}/publish", json={})
        assert second.json()["ok"] is True, second.text
        assert second.json()["status"] == "published"


# --------------------------------------------------------------------------- #
# Duplicate reporting (read-only)
# --------------------------------------------------------------------------- #
class TestDuplicateReport:
    def test_report_is_read_only_and_finds_pre_existing_duplicates(self, db, client):
        from app.services.publish_history import duplicate_history_report

        for i in range(2):
            db.add(
                PublishLog(
                    request_id=f"legacy_dup_{i}",
                    account_id="company",
                    channel_id="company_facebook",
                    platform="facebook",
                    content="duplicated content",
                    content_preview="duplicated content",
                    media_count=0,
                    publish_mode="NOW",
                    timezone="UTC",
                    status="published",
                )
            )
        db.add(
            PublishLog(
                request_id="unique_one",
                account_id="company",
                channel_id="company_facebook",
                platform="facebook",
                content="a one-off",
                content_preview="a one-off",
                media_count=0,
                publish_mode="NOW",
                timezone="UTC",
                status="published",
            )
        )
        db.commit()

        before = db.query(PublishLog).count()
        report = duplicate_history_report(db)
        after = db.query(PublishLog).count()

        # It reports, it does not clean.
        assert before == after == 3
        assert len(report) == 1
        assert report[0]["copies"] == 2
        assert set(report[0]["row_ids"]) == {
            r.id for r in db.query(PublishLog).filter(
                PublishLog.request_id.like("legacy_dup%")
            ).all()
        }

# --------------------------------------------------------------------------- #
# History removal - local bookkeeping only
# --------------------------------------------------------------------------- #
class TestHistoryRemoval:
    def test_a_single_entry_can_be_removed(self, client, fake_n8n, bound_company_channels):
        client.post("/api/publish", json=_payload())
        before = client.get("/api/publish/history").json()
        assert before["total"] >= 1
        target = before["items"][0]["id"]

        removed = client.delete(f"/api/publish/history/{target}")
        assert removed.status_code == 204, removed.text

        after = client.get("/api/publish/history").json()
        assert target not in [i["id"] for i in after["items"]]
        assert after["total"] == before["total"] - 1

    def test_removing_an_unknown_entry_is_404(self, client):
        r = client.delete("/api/publish/history/999999")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "HISTORY_ENTRY_NOT_FOUND"

    def test_removing_history_sends_nothing_to_the_engine(
        self, client, fake_n8n, bound_company_channels
    ):
        """The critical property: deleting history is a local bookkeeping act.

        A post that really went out stays live on Buffer, so the removal must
        not reach the publishing engine at all - not to edit it, not to delete
        it, not even to look at it.
        """
        client.post("/api/publish", json=_payload())
        sent_before = len(fake_n8n.calls)

        entries = client.get("/api/publish/history").json()["items"]
        client.delete(f"/api/publish/history/{entries[0]['id']}")

        assert len(fake_n8n.calls) == sent_before, (
            "removing a history row called the publishing engine; it must not"
        )

    def test_bulk_clear_requires_a_filter(self, client, fake_n8n, bound_company_channels):
        client.post("/api/publish", json=_payload())
        unscoped = client.delete("/api/publish/history")
        assert unscoped.status_code == 400
        assert unscoped.json()["error"]["code"] == "HISTORY_CLEAR_REQUIRES_FILTER"

        # Nothing was removed by the rejected request.
        assert client.get("/api/publish/history").json()["total"] >= 1

    def test_bulk_clear_only_removes_what_the_filter_matches(
        self, client, fake_n8n, bound_company_channels
    ):
        client.post("/api/publish", json=_payload(account_id="company"))
        client.post(
            "/api/publish",
            json=_payload(account_id="company", content="Second post to clear"),
        )
        assert client.get("/api/publish/history").json()["total"] >= 2

        cleared = client.delete("/api/publish/history", params={"status": "failed"})
        assert cleared.status_code == 200, cleared.text

        remaining = client.get("/api/publish/history").json()
        # Nothing failed, so nothing was deleted.
        assert remaining["total"] >= 2

    def test_bulk_clear_by_brand_removes_only_that_brand(
        self, client, fake_n8n, bound_company_channels, db
    ):
        from app.models.entities import Account, PublishLog as LogModel

        client.post("/api/publish", json=_payload())
        other = Account(id="other_brand", name="Other Brand", type="brand", active=True, sort_order=9)
        db.add(other)
        db.commit()
        db.add(
            LogModel(
                request_id="req_other_brand_1",
                account_id="other_brand",
                channel_id="company_facebook",
                platform="facebook",
                content="Belongs to another brand",
                content_preview="Belongs to another brand",
                media_count=0,
                publish_mode="NOW",
                timezone="UTC",
                status="published",
                source="dashboard",
            )
        )
        db.commit()

        cleared = client.delete("/api/publish/history", params={"account_id": "company"})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["deleted"] >= 1

        survivors = client.get("/api/publish/history").json()
        assert [i["account_id"] for i in survivors["items"]] == ["other_brand"]

    def test_the_publishing_path_never_deletes_its_own_history(
        self, client, fake_n8n, bound_company_channels
    ):
        """A failed publish must leave its record behind.

        Otherwise a post that failed after reaching Buffer could erase the only
        trace that it was ever attempted.
        """
        fake_n8n.fail(code="BUFFER_SERVER_ERROR", message="upstream exploded")
        client.post("/api/publish", json=_payload())

        history = client.get("/api/publish/history").json()
        assert history["total"] >= 1
        assert history["items"][0]["status"] == "failed"
