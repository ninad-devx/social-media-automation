"""Guard rails for the publishing safety mechanism itself.

These tests prove the isolation actually holds. If any of them fails, the suite
is no longer safe to run and no further test should be executed until it is
fixed.
"""
from __future__ import annotations

import pytest

from app.services import publishing_guard as guard


def test_guard_defaults_to_disarmed_outside_the_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production default must not intercept anything."""
    monkeypatch.delenv("PUBLISHING_GUARD", raising=False)
    assert guard.mode() == guard.MODE_OFF
    assert guard.is_armed() is False
    # Must not raise.
    guard.assert_allowed("buffer.create_post", "buffer channel abc", kind=guard.WRITE)


def test_unknown_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLISHING_GUARD", "definitely-not-a-mode")
    assert guard.mode() == guard.MODE_DRY_RUN
    with pytest.raises(guard.PublishingBlocked):
        guard.assert_allowed("buffer.create_post", "buffer channel abc", kind=guard.WRITE)


def test_suite_is_armed_by_default() -> None:
    """conftest arms dry_run for every test."""
    assert guard.mode() == guard.MODE_DRY_RUN, "the test suite must fail closed"
    assert guard.is_armed() is True


def test_write_is_blocked_and_recorded() -> None:
    before = len(guard.read_audit_log())
    with pytest.raises(guard.PublishingBlocked):
        guard.assert_allowed("buffer.create_post", "buffer channel XYZ", kind=guard.WRITE)
    records = guard.read_audit_log()
    assert len(records) == before + 1
    entry = records[-1]
    assert entry["operation"] == "buffer.create_post"
    assert entry["kind"] == "write"
    assert entry["outcome"] == "blocked"
    assert entry["mode"] == "dry_run"


def test_monitor_mode_allows_and_records(allow_publish) -> None:
    before = len(guard.read_audit_log())
    guard.assert_allowed("buffer.create_post", "buffer channel XYZ", kind=guard.WRITE)
    records = guard.read_audit_log()
    assert len(records) == before + 1
    assert records[-1]["outcome"] == "allowed"
    assert records[-1]["mode"] == "monitor"


def test_create_post_is_blocked_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real Buffer write path raises before any transport is built."""
    from app.core.config import get_settings
    from app.services.buffer_client import BufferClient

    client = BufferClient("not-a-real-token", get_settings())
    with pytest.raises(guard.PublishingBlocked):
        client.create_post(channel_id="ch_1", text="should never be sent")


def test_n8n_publish_is_blocked_end_to_end() -> None:
    from app.core.config import get_settings
    from app.services.n8n_service import N8nService

    service = N8nService(get_settings())
    assert service.is_configured() is True
    with pytest.raises(guard.PublishingBlocked):
        service.publish({"account_id": "company", "channel_id": "company_facebook"})


def test_n8n_health_probe_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A guarded read reports "unreachable" rather than exploding."""
    from app.core.config import get_settings
    from app.services.n8n_service import N8nService

    service = N8nService(get_settings())
    result = service.health()
    assert result["configured"] is True
    assert result["reachable"] is False
    assert result["error"] == "blocked_by_publishing_guard"


def test_network_kill_switch_blocks_external_hosts() -> None:
    """Belt and braces: even a bypassed guard cannot open an external socket."""
    import socket

    with pytest.raises(RuntimeError, match="Test suite blocked an outbound connection"):
        socket.create_connection(("api.buffer.com", 443), timeout=1)


def test_network_kill_switch_allows_loopback() -> None:
    """Loopback stays reachable so local sqlite/filesystem tests still work."""
    import socket

    # Binding and connecting on loopback must not raise the guard's error.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = listener.getsockname()[1]
        client = socket.socket()
        client.settimeout(2)
        client.connect(("127.0.0.1", port))
        client.close()
    finally:
        listener.close()


def test_publish_endpoint_cannot_reach_buffer_through_the_api(client) -> None:
    """A real /api/publish call is refused; it must not create a post."""
    response = client.post(
        "/api/publish",
        json={
            "account_id": "company",
            "channel_id": "company_facebook",
            "content": "SENTINEL - must never be published",
            "publish_mode": "NOW",
        },
    )
    # The seeded channels are unbound, so this is refused before any network
    # call even happens. The point of the assertion is that it is NOT 200.
    assert response.status_code != 200
    body = response.json()
    assert body.get("success") is not True


def test_internal_publish_route_is_disabled() -> None:
    """The n8n callback route must not exist in the suite."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        response = c.post(
            "/internal/publish",
            headers={"X-Internal-Secret": "anything"},
            json={
                "request_id": "req_1",
                "account_id": "company",
                "channel_id": "company_facebook",
                "content": "SENTINEL",
            },
        )
    assert response.status_code == 404


def test_guard_never_logs_a_credential() -> None:
    """Audit records must not carry a token."""
    secret = "buffer-token-that-must-never-be-logged"
    guard.arm_monitor()
    try:
        guard.assert_allowed("buffer.request", f"https://api.buffer.com/{secret}", kind=guard.WRITE)
    finally:
        guard.arm_dry_run()
    record = guard.read_audit_log()[-1]
    assert secret not in str(record)