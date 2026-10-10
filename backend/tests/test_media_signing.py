"""Tests for signed public media URLs.

Buffer downloads attachments with no credential, so these URLs are reachable
without the API key. That makes the signing the only thing standing between a
random internet scan and someone's uploaded file, which is what most of these
tests are about.
"""
from __future__ import annotations

import io
import time

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.models.entities import MediaAsset
from app.services import media_signing


def _png_bytes() -> bytes:
    """A real PNG: uploads are validated by magic bytes, not by extension."""
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_signing_key_is_derived_from_the_credential_key(settings) -> None:
    assert media_signing.has_signing_key(settings) is True


def test_sign_and_verify_round_trip(settings) -> None:
    tag = media_signing.sign("abc123.png", settings)
    assert tag.startswith("?s=")
    sig, exp = tag.lstrip("?").split("&")
    assert media_signing.verify("abc123.png", sig.split("=")[1], exp.split("=")[1], settings) is True


def test_a_different_asset_id_is_rejected(settings) -> None:
    tag = media_signing.sign("abc123.png", settings)
    sig, exp = tag.lstrip("?").split("&")
    # Replaying a valid tag against a different file must not work.
    assert media_signing.verify("other.png", sig.split("=")[1], exp.split("=")[1], settings) is False


def test_tampered_signature_is_rejected(settings) -> None:
    tag = media_signing.sign("abc123.png", settings)
    sig, exp = tag.lstrip("?").split("&")
    assert media_signing.verify("abc123.png", "notarealtagsignature", exp.split("=")[1], settings) is False


def test_expired_signature_is_rejected(settings) -> None:
    past = int(time.time()) - 60
    tag = media_signing.sign("abc123.png", settings, expires_at=past)
    sig, exp = tag.lstrip("?").split("&")
    assert media_signing.verify("abc123.png", sig.split("=")[1], exp.split("=")[1], settings) is False


def test_upload_returns_a_signed_public_url(client: TestClient, settings) -> None:
    r = client.post(
        "/api/media/upload",
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    url = r.json()["url"]
    assert "?s=" in url and "&e=" in url


def test_public_url_serves_the_file_without_an_api_key(client: TestClient) -> None:
    r = client.post(
        "/api/media/upload",
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    url = r.json()["url"]
    path = url.split("?")[0].split("/api/")[1]
    query = url.split("?", 1)[1]

    # No X-API-Key: this is exactly Buffer's situation.
    got = client.get(f"/api/{path}?{query}")
    assert got.status_code == 200
    assert got.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_public_url_without_a_signature_is_404(client: TestClient) -> None:
    r = client.post(
        "/api/media/upload",
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    asset_id = r.json()["asset_id"]
    bare = client.get(f"/api/media/public/{asset_id}")
    assert bare.status_code == 404


def test_public_url_with_a_forged_signature_is_404(client: TestClient) -> None:
    r = client.post(
        "/api/media/upload",
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    asset_id = r.json()["asset_id"]
    forged = client.get(f"/api/media/public/{asset_id}?s=AAAAAAAAAAAAAAAAAAAAAAAA&e=99999999999")
    assert forged.status_code == 404


def test_authenticated_route_still_requires_the_key(client: TestClient, db: Session, monkeypatch) -> None:
    """The signed route must not weaken the authenticated one."""
    from app.core.config import get_settings

    r = client.post(
        "/api/media/upload",
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    asset_id = r.json()["asset_id"]
    monkeypatch.setattr(get_settings(), "api_key", "a-real-secret-key")
    assert db.query(MediaAsset).filter(MediaAsset.asset_id == asset_id).count() == 1
    assert client.get(f"/api/media/{asset_id}").status_code == 401
