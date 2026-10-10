"""Tests for POST /api/publish.

The n8n engine is always faked here, so nothing is published for real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeN8n


def _local(offset_minutes: int) -> str:
    """A naive local datetime, `offset_minutes` from now, in Asia/Dhaka."""
    tz = timezone(timedelta(hours=6))
    return (datetime.now(tz) + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%dT%H:%M:%S")


def _payload(**overrides) -> dict:
    base = {
        "account_id": "company",
        "channel_id": "company_facebook",
        "content": "Hello world",
        "media": [],
        "publish_mode": "NOW",
        "scheduled_at": None,
        "timezone": "Asia/Dhaka",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# happy paths
# --------------------------------------------------------------------------
def test_publish_now(client: TestClient, fake_n8n: FakeN8n) -> None:
    r = client.post("/api/publish", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["success"] is True
    assert body["status"] == "published"
    assert body["account_id"] == "company"
    assert body["channel_id"] == "company_facebook"
    assert body["platform"] == "facebook"
    assert body["account_name"] == "Company"
    assert body["buffer_publication_id"] == "buf_test_123"
    assert body["request_id"].startswith("req_")
    assert body["content_length"] == len("Hello world")

    # the engine received exactly the documented contract
    sent = fake_n8n.calls[0]
    assert set(sent) == {
        "account_id", "channel_id", "content",
        "media", "publish_mode", "scheduled_at", "timezone",
    }
    assert sent["publish_mode"] == "NOW"
    assert sent["scheduled_at"] is None


def test_publish_schedule(client: TestClient, fake_n8n: FakeN8n) -> None:
    fake_n8n.succeed_scheduled()
    when = _local(120)

    r = client.post(
        "/api/publish",
        json=_payload(publish_mode="SCHEDULE", scheduled_at=when),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["status"] == "scheduled"
    assert body["timezone"] == "Asia/Dhaka"
    assert body["scheduled_at"] is not None

    # The backend must NOT wait for the scheduled time; it returns immediately.
    assert fake_n8n.calls[0]["publish_mode"] == "SCHEDULE"
    assert fake_n8n.calls[0]["scheduled_at"] == when


def test_schedule_timezone_conversion_is_delegated_to_n8n(client: TestClient, fake_n8n: FakeN8n) -> None:
    """We send naive local time + timezone; n8n owns the UTC conversion."""
    fake_n8n.succeed_scheduled()
    when = _local(60)
    client.post(
        "/api/publish",
        json=_payload(
            publish_mode="SCHEDULE",
            scheduled_at=when,
            timezone="Asia/Dhaka",
            channel_id="company_linkedin",
        ),
    )
    sent = fake_n8n.calls[0]
    assert sent["scheduled_at"] == when
    assert sent["timezone"] == "Asia/Dhaka"
    assert "Z" not in sent["scheduled_at"]  # never blindly append UTC


def test_publish_with_image_media(client: TestClient, fake_n8n: FakeN8n) -> None:
    r = client.post(
        "/api/publish",
        json=_payload(
            channel_id="company_instagram",
            media=[{"type": "image", "url": "https://example.com/a.jpg", "alt_text": "Alt"}],
        ),
    )
    assert r.status_code == 200, r.text
    assert r.json()["media_count"] == 1
    assert fake_n8n.calls[0]["media"][0]["url"] == "https://example.com/a.jpg"


def test_publish_trims_but_does_not_rewrite_content(client: TestClient, fake_n8n: FakeN8n) -> None:
    content = "  Hello   world 🚀  #tag  "
    r = client.post("/api/publish", json=_payload(content=content))
    assert r.status_code == 200
    sent = fake_n8n.calls[0]["content"]
    assert sent == "Hello   world 🚀  #tag"  # trimmed only, internal spacing kept
    assert "🚀" in sent


# --------------------------------------------------------------------------
# validation: the engine must never be called
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload, expected_code, expected_status",
    [
        (_payload(account_id="ghost_brand"), "ACCOUNT_NOT_FOUND", 404),
        (_payload(channel_id="ghost_channel"), "CHANNEL_NOT_FOUND", 404),
        (_payload(content=""), "CONTENT_EMPTY", 400),
        (_payload(content="    "), "CONTENT_EMPTY", 400),
        (_payload(publish_mode="LATER"), "INVALID_PUBLISH_MODE", 422),
        (_payload(timezone="Mars/Olympus"), "INVALID_TIMEZONE", 422),
        (_payload(publish_mode="SCHEDULE", scheduled_at=None), "INVALID_SCHEDULE", 400),
        (_payload(publish_mode="SCHEDULE", scheduled_at="2020-01-01T10:00:00"), "SCHEDULE_IN_PAST", 400),
        (_payload(publish_mode="SCHEDULE", scheduled_at="not-a-date"), "INVALID_SCHEDULE", 422),
        (_payload(media="a.jpg"), "MEDIA_INVALID", 422),
        (_payload(media=[{"type": "gif", "url": "https://x.test/a.gif"}]), "MEDIA_INVALID", 422),
        (_payload(media=[{"type": "image", "url": "not-a-url"}]), "MEDIA_INVALID", 422),
        (_payload(channel_id="company_instagram", media=[]), "MEDIA_INVALID", 400),
    ],
)
def test_validation_rejects_before_calling_n8n(
    client: TestClient, fake_n8n: FakeN8n, payload: dict, expected_code, expected_status
) -> None:
    r = client.post("/api/publish", json=payload)
    assert r.status_code == expected_status, r.text
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == expected_code
    # the critical guarantee: the publishing engine was never touched
    assert fake_n8n.calls == []


def test_channel_from_another_account_is_rejected(client: TestClient, fake_n8n: FakeN8n) -> None:
    """company + a channel owned by a different account must never publish."""
    from app.database.session import session_scope
    from app.models.entities import Account, Channel

    with session_scope() as db:
        db.add(Account(id="ceo", name="CEO", type="person", active=True))
        db.flush()
        db.add(
            Channel(
                id="ceo_instagram",
                account_id="ceo",
                platform="instagram",
                display_name="Instagram",
                active=True,
            )
        )

    r = client.post("/api/publish", json=_payload(account_id="company", channel_id="ceo_instagram"))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "CHANNEL_NOT_BELONG_TO_ACCOUNT"
    assert fake_n8n.calls == []


def test_inactive_channel_is_rejected(client: TestClient, fake_n8n: FakeN8n) -> None:
    from app.database.session import session_scope
    from app.models.entities import Channel

    with session_scope() as db:
        ch = db.get(Channel, "company_facebook")
        ch.active = False

    r = client.post("/api/publish", json=_payload())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CHANNEL_INACTIVE"
    assert fake_n8n.calls == []


def test_schedule_beyond_horizon_is_rejected(client: TestClient, fake_n8n: FakeN8n) -> None:
    far = (datetime.now(timezone.utc) + timedelta(days=400)).astimezone(
        timezone(timedelta(hours=6))
    ).strftime("%Y-%m-%dT%H:%M:%S")
    r = client.post("/api/publish", json=_payload(publish_mode="SCHEDULE", scheduled_at=far))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SCHEDULE_TOO_FAR"
    assert fake_n8n.calls == []


# --------------------------------------------------------------------------
# upstream failures are normalised
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "upstream, expected_code, expected_status",
    [
        ("BUFFER_AUTH_ERROR", "BUFFER_AUTH_ERROR", 502),
        ("BUFFER_RATE_LIMIT", "BUFFER_RATE_LIMIT", 502),
        ("BUFFER_BAD_REQUEST", "BUFFER_BAD_REQUEST", 502),
        ("BUFFER_SERVER_ERROR", "BUFFER_SERVER_ERROR", 502),
        ("BUFFER_NETWORK_ERROR", "BUFFER_NETWORK_ERROR", 502),
        ("PUBLISHING_TIMEOUT", "PUBLISHING_TIMEOUT", 502),
        ("PUBLISHING_UNAVAILABLE", "PUBLISHING_UNAVAILABLE", 502),
    ],
)
def test_upstream_errors_are_normalized(
    client: TestClient, fake_n8n: FakeN8n, upstream: str, expected_code: str, expected_status: int
) -> None:
    fake_n8n.fail(upstream)
    r = client.post("/api/publish", json=_payload())
    assert r.status_code == expected_status
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == expected_code
    # no internals leaked
    assert "traceback" not in r.text.lower()
    assert "Buffer returned" not in r.text
    assert "api.buffer.com" not in r.text


def test_failure_is_recorded_in_history(client: TestClient, fake_n8n: FakeN8n) -> None:
    fake_n8n.fail("BUFFER_SERVER_ERROR")
    client.post("/api/publish", json=_payload())

    hist = client.get("/api/publish/history").json()
    assert hist["total"] == 1
    row = hist["items"][0]
    assert row["status"] == "failed"
    assert row["error_code"] == "BUFFER_SERVER_ERROR"


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------
def test_duplicate_submission_is_prevented_by_idempotency_key(
    client: TestClient, fake_n8n: FakeN8n
) -> None:
    headers = {"Idempotency-Key": "abc-123"}
    first = client.post("/api/publish", json=_payload(), headers=headers)
    assert first.status_code == 200
    second = client.post("/api/publish", json=_payload(), headers=headers)

    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert second.json()["buffer_publication_id"] == first.json()["buffer_publication_id"]
    # the engine was only called once
    assert len(fake_n8n.calls) == 1


def test_same_key_different_payload_is_rejected(client: TestClient, fake_n8n: FakeN8n) -> None:
    headers = {"Idempotency-Key": "abc-123"}
    client.post("/api/publish", json=_payload(), headers=headers)
    r = client.post("/api/publish", json=_payload(content="different"), headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    assert len(fake_n8n.calls) == 1


def test_without_idempotency_key_two_calls_publish_twice(client: TestClient, fake_n8n: FakeN8n) -> None:
    client.post("/api/publish", json=_payload())
    client.post("/api/publish", json=_payload())
    assert len(fake_n8n.calls) == 2


# --------------------------------------------------------------------------
# history / calendar foundation
# --------------------------------------------------------------------------
def test_history_records_successful_publishes(client: TestClient, fake_n8n: FakeN8n) -> None:
    client.post("/api/publish", json=_payload())
    client.post("/api/publish", json=_payload(channel_id="company_linkedin", content="Second post"))

    hist = client.get("/api/publish/history").json()
    assert hist["total"] == 2
    platforms = {row["platform"] for row in hist["items"]}
    assert platforms == {"facebook", "linkedin"}


def test_history_filters_by_account(client: TestClient, fake_n8n: FakeN8n) -> None:
    client.post("/api/publish", json=_payload())
    hist = client.get("/api/publish/history?account_id=ghost_brand").json()
    assert hist["total"] == 0


# --------------------------------------------------------------------------
# request id propagation
# --------------------------------------------------------------------------
def test_request_id_is_echoed_and_forwarded(client: TestClient, fake_n8n: FakeN8n) -> None:
    r = client.post("/api/publish", json=_payload(), headers={"X-Request-Id": "req_custom_123"})
    assert r.status_code == 200
    assert r.json()["request_id"] == "req_custom_123"
    assert r.headers["X-Request-Id"] == "req_custom_123"
    # n8n's own id is surfaced separately for log correlation
    assert r.json()["n8n_request_id"] == "pub_N8NTEST"