"""Tests for Buffer request shaping: HTTP verbs on media and dueAt format."""
from __future__ import annotations

import io
import struct
import zlib

from fastapi.testclient import TestClient
from PIL import Image


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def test_public_media_answers_head(client: TestClient) -> None:
    """Buffer probes a media URL with HEAD before fetching it.

    With only GET registered this returned 405 and Buffer reported
    "Please update the media URL to be publicly accessible" for a URL that
    returned 200 on GET.
    """
    r = client.post(
        "/api/media/upload",
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    url = r.json()["url"]
    path = url.split("?")[0].split("/api/")[1]
    query = url.split("?", 1)[1]

    head = client.head(f"/api/{path}?{query}")
    assert head.status_code == 200
    assert head.headers.get("content-type") == "image/png"


def test_authenticated_media_answers_head(client: TestClient) -> None:
    r = client.post(
        "/api/media/upload",
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    asset_id = r.json()["asset_id"]
    assert client.head(f"/api/media/{asset_id}").status_code == 200


def test_head_still_requires_a_valid_signature(client: TestClient) -> None:
    """Answering HEAD must not weaken the signature check."""
    r = client.post(
        "/api/media/upload",
        files={"file": ("probe.png", _png_bytes(), "image/png")},
    )
    asset_id = r.json()["asset_id"]
    assert client.head(f"/api/media/public/{asset_id}").status_code == 404


def test_due_at_is_converted_to_epoch_seconds() -> None:
    """Buffer's DateTime scalar is epoch seconds, not an ISO string."""
    from datetime import datetime, timezone

    from app.services.buffer_client import _due_at_epoch

    epoch = _due_at_epoch("2026-10-11T02:08:00", "UTC")
    assert isinstance(epoch, int)
    assert datetime.fromtimestamp(epoch, timezone.utc).isoformat() == "2026-10-11T02:08:00+00:00"


def test_due_at_respects_the_operator_timezone() -> None:
    """The same wall-clock time in a different zone is a different instant."""
    from datetime import datetime, timezone

    from app.services.buffer_client import _due_at_epoch

    dhaka = _due_at_epoch("2026-10-11T02:08:00", "Asia/Dhaka")
    utc = _due_at_epoch("2026-10-11T02:08:00", "UTC")
    assert utc - dhaka == 6 * 3600  # Bangladesh is UTC+6 year round


def test_due_at_rejects_unparseable_input() -> None:
    from app.services.buffer_client import BufferApiError, _due_at_epoch

    for bad in ["", "   ", "not-a-time", "2026-13-45T99:99:99"]:
        try:
            _due_at_epoch(bad, "UTC")
        except BufferApiError:
            continue
        raise AssertionError(f"{bad!r} should have been rejected")


def test_due_at_defaults_to_utc_not_machine_local() -> None:
    """A container's local zone is never the operator's zone."""
    from datetime import datetime, timezone

    from app.services.buffer_client import _due_at_epoch

    epoch = _due_at_epoch("2026-10-11T02:08:00", None)
    assert datetime.fromtimestamp(epoch, timezone.utc).hour == 2
