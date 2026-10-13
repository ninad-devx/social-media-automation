"""Media that actually reaches Buffer, and Quick Publish reaching Campaigns.

The original symptom: media uploaded locally never appeared on the published
post. Buffer downloads attachments itself, asynchronously, so an unreachable
URL produces a post with no image and no error - a silent failure.
"""
from __future__ import annotations

import httpx
import pytest

from app.services.platform_limits import (
    media_capabilities,
    requires_media,
    validate_media_for_platform,
)
from app.services.storage import verify_publicly_reachable


# --------------------------------------------------------------------------- #
# reachability
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status_code: int, content_type: str | None = None):
        self.status_code = status_code
        self.headers = {"content-type": content_type} if content_type else {}


def _patch_get(monkeypatch, handler):
    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            return handler(url)

    monkeypatch.setattr(httpx, "Client", _Client)


def test_a_reachable_image_passes(monkeypatch) -> None:
    _patch_get(monkeypatch, lambda url: _Resp(200, "image/png"))
    ok, reason = verify_publicly_reachable(["https://cdn.example.com/a.png"])
    assert ok is True
    assert reason is None


def test_a_404_is_rejected_with_an_explanation(monkeypatch) -> None:
    _patch_get(monkeypatch, lambda url: _Resp(404))
    ok, reason = verify_publicly_reachable(["https://cdn.example.com/gone.png"])
    assert ok is False
    assert "404" in reason
    # The message has to say the post would go out without the image.
    assert "without the image" in reason


def test_an_html_interstitial_is_caught(monkeypatch) -> None:
    """A tunnel warning page returns 200 text/html, which Buffer cannot use."""
    _patch_get(monkeypatch, lambda url: _Resp(200, "text/html"))
    ok, reason = verify_publicly_reachable(["https://tunnel.example.com/a.png"])
    assert ok is False
    assert "text/html" in reason
    assert "warning page" in reason


def test_a_dead_host_is_rejected(monkeypatch) -> None:
    def boom(url):
        raise httpx.ConnectError("no route to host")

    _patch_get(monkeypatch, boom)
    ok, reason = verify_publicly_reachable(["https://gone.example.com/a.png"])
    assert ok is False
    assert "public" in reason.lower()


def test_a_non_http_url_is_rejected_without_any_request(monkeypatch) -> None:
    def boom(url):
        raise AssertionError("should not make a request")

    _patch_get(monkeypatch, boom)
    ok, reason = verify_publicly_reachable(["file:///etc/passwd"])
    assert ok is False
    assert "not a web URL" in reason


def test_every_attachment_is_checked(monkeypatch) -> None:
    seen = []

    def handler(url):
        seen.append(url)
        return _Resp(404)

    _patch_get(monkeypatch, handler)
    ok, _ = verify_publicly_reachable(["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"])
    assert ok is False
    # It stops at the first failure rather than hammering the host.
    assert len(seen) == 1


def test_an_empty_list_is_trivially_fine(monkeypatch) -> None:
    _patch_get(monkeypatch, lambda url: pytest.fail("should not be called"))
    assert verify_publicly_reachable([]) == (True, None)


# --------------------------------------------------------------------------- #
# publish blocks unreachable media
# --------------------------------------------------------------------------- #
def test_publishing_unreachable_media_is_refused(client, fake_n8n, monkeypatch, bound_company_channels) -> None:
    """The whole point: fail loudly instead of publishing a post with no image."""
    from app.services import storage as storage_module

    monkeypatch.setattr(
        storage_module,
        "verify_publicly_reachable",
        lambda urls, timeout=12.0: (False, "Buffer cannot reach https://x/a.png"),
    )

    r = client.post(
        "/api/publish",
        json={
            "account_id": "company",
            "channel_id": "company_linkedin",
            "content": "Hello",
            "media": [{"type": "image", "url": "https://x/a.png"}],
            "publish_mode": "NOW",
            "timezone": "Asia/Dhaka",
        },
    )

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_NOT_ACCESSIBLE"
    # Nothing was handed to n8n.
    assert fake_n8n.calls == []


def test_reachable_media_still_publishes(client, fake_n8n, bound_company_channels) -> None:
    r = client.post(
        "/api/publish",
        json={
            "account_id": "company",
            "channel_id": "company_linkedin",
            "content": "Hello",
            "media": [{"type": "image", "url": "https://cdn.example.com/a.png"}],
            "publish_mode": "NOW",
            "timezone": "Asia/Dhaka",
        },
    )
    assert r.status_code == 200, r.text
    assert len(fake_n8n.calls) == 1
    assert fake_n8n.calls[0]["media"][0]["url"] == "https://cdn.example.com/a.png"


# --------------------------------------------------------------------------- #
# per-platform media capability
# --------------------------------------------------------------------------- #
def test_instagram_supports_carousel_but_linkedin_does_not() -> None:
    caps = media_capabilities("instagram")
    assert caps["supports_carousel"] is True
    assert caps["max_assets"] == 10

    li = media_capabilities("linkedin")
    assert li["supports_carousel"] is False
    assert li["max_assets"] == 1


def test_a_carousel_on_linkedin_is_refused() -> None:
    problems = validate_media_for_platform(
        "linkedin", [{"type": "image", "url": "a"}, {"type": "image", "url": "b"}]
    )
    # The single rule set now phrases this with the actual limit rather than a
    # generic "one attachment", so the operator sees the number.
    assert any("at most 1 attachment" in p for p in problems)


def test_a_valid_instagram_carousel_is_accepted() -> None:
    problems = validate_media_for_platform(
        "instagram", [{"type": "image", "url": f"https://x/{i}.png"} for i in range(4)]
    )
    assert problems == []


def test_too_many_assets_are_refused_with_the_limit_stated() -> None:
    problems = validate_media_for_platform(
        "linkedin", [{"type": "image", "url": f"https://x/{i}.png"} for i in range(3)]
    )
    assert any("at most 1" in p for p in problems)


def test_mixing_video_and_images_is_refused() -> None:
    problems = validate_media_for_platform(
        "instagram",
        [{"type": "image", "url": "a"}, {"type": "video", "url": "b"}],
    )
    assert any("mix" in p.lower() for p in problems)


def test_instagram_without_media_is_refused() -> None:
    assert requires_media("instagram") is True
    problems = validate_media_for_platform("instagram", [])
    assert any("needs at least 1 attachment" in p for p in problems), problems


def test_an_attachment_without_a_url_is_refused() -> None:
    problems = validate_media_for_platform("facebook", [{"type": "image", "url": "  "}])
    assert any("working file URL" in p for p in problems)


def test_capabilities_handle_an_unknown_platform() -> None:
    caps = media_capabilities("some-new-network")
    assert caps["label"] == "Social"
    assert caps["supports_carousel"] is False


# --------------------------------------------------------------------------- #
# Quick Publish appears in Campaigns
# --------------------------------------------------------------------------- #
def test_quick_publish_is_recorded_as_a_campaign(client, fake_n8n, bound_company_channels) -> None:
    r = client.post(
        "/api/publish",
        json={
            "account_id": "company",
            "channel_id": "company_linkedin",
            "content": "A hand-written post.",
            "media": [{"type": "image", "url": "https://cdn.example.com/a.png"}],
            "publish_mode": "NOW",
            "timezone": "Asia/Dhaka",
        },
    )
    assert r.status_code == 200, r.text

    page = client.get("/api/ai/campaigns").json()
    assert len(page["items"]) == 1

    campaign = client.get("/api/ai/campaigns/" + page["items"][0]["id"]).json()
    assert campaign["source"] == "quick_publish"
    assert campaign["status"] == "published"

    post = campaign["posts"][0]
    assert post["effective_content"] == "A hand-written post."
    assert post["publishing_status"] == "published"
    assert len(post["media"]) == 1


def test_quick_publish_without_media_is_still_recorded(client, fake_n8n, bound_company_channels) -> None:
    client.post(
        "/api/publish",
        json={
            "account_id": "company",
            "channel_id": "company_facebook",
            "content": "No picture needed.",
            "media": [],
            "publish_mode": "NOW",
            "timezone": "Asia/Dhaka",
        },
    )
    first = client.get("/api/ai/campaigns").json()["items"][0]
    campaign = client.get("/api/ai/campaigns/" + first["id"]).json()
    assert campaign["posts"][0]["media"] == []


def test_a_failing_publish_creates_no_campaign(client, fake_n8n, bound_company_channels) -> None:
    fake_n8n.fail(code="BUFFER_SERVER_ERROR")
    r = client.post(
        "/api/publish",
        json={
            "account_id": "company",
            "channel_id": "company_linkedin",
            "content": "Should not be recorded.",
            "media": [],
            "publish_mode": "NOW",
            "timezone": "Asia/Dhaka",
        },
    )
    assert r.status_code in (200, 502)
    assert client.get("/api/ai/campaigns").json()["items"] == []


def test_campaigns_can_be_filtered_by_source(client, fake_n8n, bound_company_channels) -> None:
    client.post(
        "/api/publish",
        json={
            "account_id": "company",
            "channel_id": "company_linkedin",
            "content": "Quick",
            "media": [],
            "publish_mode": "NOW",
            "timezone": "Asia/Dhaka",
        },
    )
    client.post(
        "/api/ai/campaigns",
        json={
            "topic": "Generated",
            "targets": [{"account_id": "company", "channel_id": "company_facebook"}],
        },
    )

    everything = client.get("/api/ai/campaigns").json()["items"]
    assert {c["source"] for c in everything} == {"quick_publish", "ai_campaign"}


def test_ai_campaigns_are_tagged_as_such(client, fake_adapter, provider_row, bound_company_channels) -> None:
    from tests.conftest import make_campaign

    campaign = make_campaign(client, [{"account_id": "company", "channel_id": "company_facebook"}])
    body = client.get(f"/api/ai/campaigns/{campaign['id']}").json()
    assert body["source"] == "ai_campaign"


# --------------------------------------------------------------------------- #
# media editing on a campaign post
# --------------------------------------------------------------------------- #
def test_media_can_be_replaced_on_a_campaign_post(client, fake_adapter, provider_row, bound_company_channels) -> None:
    from tests.conftest import make_campaign, wait_for_generation

    campaign = make_campaign(client, [{"account_id": "company", "channel_id": "company_linkedin"}])
    client.post(f"/api/ai/campaigns/{campaign['id']}/generate", json={})
    wait_for_generation()

    post = client.get(f"/api/ai/campaigns/{campaign['id']}").json()["posts"][0]
    assert len(post["media"]) == 0

    r = client.patch(
        f"/api/ai/campaign-posts/{post['id']}",
        json={"media": [{"type": "image", "url": "https://cdn.example.com/x.png"}]},
    )
    assert r.status_code == 200
    assert len(r.json()["media"]) == 1


def test_media_capabilities_endpoint(client) -> None:
    r = client.get("/api/ai/media-capabilities", params={"platform": "instagram"})
    assert r.status_code == 200
    body = r.json()
    assert body["supports_carousel"] is True
    assert body["requires_media"] is True