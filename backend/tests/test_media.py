"""Tests for media upload, serving and deletion.

Covers the security-relevant behaviour: magic-byte validation, path traversal
refusal, and the size ceiling.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 20
GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00,"

# A file that lies: .png extension and image/png content type, but is text.
DISGUISED = b"<?php system($_GET['c']); ?>" * 4


def upload(client: TestClient, data: bytes, filename: str = "a.png", content_type: str = "image/png"):
    return client.post(
        "/api/media/upload",
        files={"file": (filename, io.BytesIO(data), content_type)},
    )


def test_upload_png_returns_asset(client: TestClient) -> None:
    r = upload(client, PNG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "image"
    assert body["mime"] == "image/png"
    assert body["size"] == len(PNG)
    assert body["asset_id"].endswith(".png")
    assert body["url"]


def test_uploaded_file_can_be_fetched(client: TestClient) -> None:
    asset_id = upload(client, PNG).json()["asset_id"]
    r = client.get(f"/api/media/{asset_id}")
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"].startswith("image/png")
    # Security headers: the browser must not sniff this into something else.
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]


def test_upload_jpeg_and_gif_detected_by_magic_bytes(client: TestClient) -> None:
    """Content type comes from the bytes, not the filename or header."""
    jpeg = upload(client, JPEG, filename="weird.txt", content_type="text/plain")
    assert jpeg.status_code == 200
    assert jpeg.json()["mime"] == "image/jpeg"

    gif = upload(client, GIF, filename="x.anything", content_type="application/octet-stream")
    assert gif.json()["mime"] == "image/gif"


def test_disguised_file_is_rejected(client: TestClient) -> None:
    """A text file renamed to .png must not be accepted."""
    r = upload(client, DISGUISED, filename="evil.png", content_type="image/png")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_INVALID"


def test_empty_upload_is_rejected(client: TestClient) -> None:
    r = upload(client, b"")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_INVALID"


def test_oversized_upload_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_bytes", 64)

    r = upload(client, PNG + b"\x00" * 200)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_INVALID"


def test_original_filename_is_never_used_for_the_path(client: TestClient) -> None:
    """A traversal filename must not influence where the file is written."""
    r = upload(client, PNG, filename="../../../evil.png")
    assert r.status_code == 200
    asset_id = r.json()["asset_id"]
    # generated id, no path separators, no traversal
    assert "/" not in asset_id and "\\" not in asset_id and ".." not in asset_id
    assert client.get(f"/api/media/{asset_id}").status_code == 200


@pytest.mark.parametrize(
    "asset_id",
    ["../../secret.env", "..%2Fsecret", "a/b", "a\\b", "does-not-exist"],
)
def test_path_traversal_and_unknown_ids_are_refused(client: TestClient, asset_id: str) -> None:
    r = client.get(f"/api/media/{asset_id}")
    assert r.status_code in (400, 404)


def test_delete_removes_the_file(client: TestClient) -> None:
    asset_id = upload(client, PNG).json()["asset_id"]
    assert client.get(f"/api/media/{asset_id}").status_code == 200

    assert client.delete(f"/api/media/{asset_id}").status_code == 204
    assert client.get(f"/api/media/{asset_id}").status_code == 404


def test_list_media(client: TestClient) -> None:
    upload(client, PNG, filename="one.png")
    upload(client, JPEG, filename="two.jpg")

    r = client.get("/api/media")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert all({"asset_id", "filename", "kind", "url", "public"} <= set(i) for i in items)
    assert {i["filename"] for i in items} == {"one.png", "two.jpg"}


def test_upload_reports_public_reachability(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without PUBLIC_MEDIA_BASE_URL the UI must be warned Buffer can't fetch it."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "public_media_base_url", "")
    body = upload(client, PNG).json()
    assert body["publicly_reachable"] is False
    assert body["warning"]
    assert "Buffer" in body["warning"]


def test_preview_url_is_fetchable_without_any_auth_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview is rendered by an <img> tag, which cannot send X-API-Key.

    The preview URL used to point at the authenticated /api/media/{id} route,
    so every thumbnail returned 401 and rendered broken. Both `url` and
    `preview_url` are now signed public URLs, which a plain GET can fetch.
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "public_media_base_url", "https://api.example.com/api/media/public")
    monkeypatch.setattr(get_settings(), "preview_media_base_url", "https://api.example.com/api/media")

    body = upload(client, PNG).json()
    assert body["url"].startswith("https://api.example.com/api/media/public/")
    assert body["preview_url"] == body["url"]
    assert "?s=" in body["preview_url"] and "&e=" in body["preview_url"]

    # The real test: fetch it exactly as a browser <img> would.
    path = body["preview_url"].split("?")[0].split("/api/")[1]
    query = body["preview_url"].split("?", 1)[1]
    fetched = client.get(f"/api/{path}?{query}")
    assert fetched.status_code == 200
    assert fetched.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_upload_warns_nothing_when_public_url_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "public_media_base_url", "https://cdn.example.com/api/media")

    body = upload(client, PNG).json()
    assert body["publicly_reachable"] is True
    assert body["warning"] is None
    assert body["url"].startswith("https://cdn.example.com/api/media/")


def test_uploaded_url_can_be_used_in_a_publish_request(
    client: TestClient, fake_n8n, bound_company_channels
) -> None:
    """End-to-end: upload, then publish using the returned URL."""
    media = upload(client, PNG).json()

    r = client.post(
        "/api/publish",
        json={
            "account_id": "company",
            "channel_id": "company_instagram",
            "content": "With an uploaded image",
            "media": [{"type": "image", "url": media["url"], "asset_id": media["asset_id"]}],
            "publish_mode": "NOW",
            "scheduled_at": None,
            "timezone": "Asia/Dhaka",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["media_count"] == 1
    # the engine received the uploaded URL
    assert fake_n8n.calls[0]["media"][0]["url"] == media["url"]