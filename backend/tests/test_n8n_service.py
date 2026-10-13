"""Tests for the n8n adapter's transport handling.

These exercise N8nService directly against a mock HTTP transport.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings
from app.services.n8n_service import N8nService, build_n8n_payload


def _service(handler) -> N8nService:
    settings = get_settings()
    transport = httpx.MockTransport(handler)
    svc = N8nService(settings)
    # patch the client's transport factory
    original = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    svc._client_factory = factory  # type: ignore[attr-defined]
    return svc


@pytest.fixture(autouse=True)
def patch_client(monkeypatch, allow_publish):
    """Every test in this module exercises the real N8nService.publish().

    The transport is mocked, but the publishing guard is armed at the service
    seam, so `allow_publish` is required to switch it from `dry_run` to
    `monitor`. The suite-wide network kill switch still makes a real request
    to an n8n instance impossible.
    """

    holder: dict = {}

    def factory(handler):
        settings = get_settings()
        transport = httpx.MockTransport(handler)
        original = httpx.Client

        def client(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", client)
        return N8nService(settings)

    holder["factory"] = factory
    return holder


def test_success_response_is_normalized(patch_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "request_id": "pub_ABC",
                "status": "published",
                "buffer_publication_id": "buf_1",
                "external_link": "https://facebook.com/x/1",
            },
        )

    svc = patch_client["factory"](handler)
    result = svc.publish({"account_id": "company"})

    assert result.success is True
    assert result.buffer_publication_id == "buf_1"
    assert result.n8n_request_id == "pub_ABC"
    assert result.status == "published"


def test_domain_error_is_reported_not_raised(patch_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": False,
                "request_id": "pub_ABC",
                "error": {"code": "ACCOUNT_NOT_FOUND", "message": "nope"},
            },
        )

    svc = patch_client["factory"](handler)
    result = svc.publish({"account_id": "ghost"})
    assert result.success is False
    assert result.upstream_code == "ACCOUNT_NOT_FOUND"


def test_rate_limit_status_maps_to_rate_limit(patch_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"errors": [{"message": "slow down"}]})

    svc = patch_client["factory"](handler)
    result = svc.publish({})
    assert result.upstream_code == "BUFFER_RATE_LIMIT"


def test_unauthorized_status_maps_to_auth_error(patch_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    svc = patch_client["factory"](handler)
    result = svc.publish({})
    assert result.upstream_code == "BUFFER_AUTH_ERROR"


def test_server_error_maps_to_server_error(patch_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    svc = patch_client["factory"](handler)
    result = svc.publish({})
    assert result.upstream_code == "BUFFER_SERVER_ERROR"


def test_network_failure_maps_to_network_error(patch_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    svc = patch_client["factory"](handler)
    result = svc.publish({})
    assert result.upstream_code == "BUFFER_NETWORK_ERROR"


def test_timeout_maps_to_timeout(patch_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    svc = patch_client["factory"](handler)
    result = svc.publish({})
    assert result.upstream_code == "PUBLISHING_TIMEOUT"


def test_non_json_response_does_not_raise(patch_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    svc = patch_client["factory"](handler)
    result = svc.publish({})
    assert result.success is False


def test_secret_header_is_sent_when_configured(patch_client, monkeypatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"success": True, "status": "published"})

    svc = patch_client["factory"](handler)
    svc._settings.n8n_webhook_secret = "s3cret"  # type: ignore[attr-defined]
    svc.publish({})

    assert seen["headers"].get("x-n8n-secret") == "s3cret"


def test_build_payload_shape() -> None:
    payload = build_n8n_payload(
        {
            "account_id": "company",
            "channel_id": "company_instagram",
            "content": "hi",
            "media": [{"type": "image", "url": "https://x.test/a.jpg", "alt_text": "a"}],
            "publish_mode": "SCHEDULE",
            "scheduled_at": "2026-10-12T20:00:00",
            "timezone": "Asia/Dhaka",
        }
    )
    assert payload["media"] == [{"type": "image", "url": "https://x.test/a.jpg", "alt_text": "a"}]
    assert payload["scheduled_at"] == "2026-10-12T20:00:00"