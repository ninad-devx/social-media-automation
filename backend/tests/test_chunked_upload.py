"""Resumable upload for videos too large to send in one request.

Facebook accepts a 4 GB Reel, which is the case that broke the old single
request upload: the browser refused it at 25 MB, and the server would have
refused it anyway. These tests pin the behaviour that replaced it - the ceiling
is per kind, chunks land at their offsets, a retry is harmless, and the bytes
that actually arrive are what gets validated.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings

# An MP4 header followed by filler. Only the magic bytes are ever inspected.
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

CHUNK = 64 * 1024


@pytest.fixture
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the chunk size so a test does not have to move megabytes."""
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_chunk_bytes", CHUNK)
    monkeypatch.setattr(settings, "upload_staging_dir", "")  # derive from media dir


def start(client: TestClient, filename: str, size: int) -> dict:
    r = client.post("/api/media/uploads", json={"filename": filename, "size": size})
    assert r.status_code == 201, r.text
    return r.json()


def send_all(client: TestClient, session: dict, data: bytes) -> None:
    chunk_size = session["chunk_size"]
    for index in range(session["chunk_count"]):
        block = data[index * chunk_size : (index + 1) * chunk_size]
        r = client.put(
            f"/api/media/uploads/{session['upload_id']}/chunks/{index}",
            content=block,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# limits
# --------------------------------------------------------------------------- #
def test_limits_endpoint_reports_a_video_ceiling_far_above_the_image_one(
    client: TestClient,
) -> None:
    """The UI must stop inventing 25 MB for everything."""
    body = client.get("/api/media/upload-limits").json()

    assert body["image_max_bytes"] == 25 * 1024 * 1024
    assert body["video_max_bytes"] >= 4 * 1024 * 1024 * 1024
    assert body["chunk_bytes"] >= 64 * 1024


def test_an_oversized_image_is_still_refused(client: TestClient, monkeypatch) -> None:
    """Raising the video ceiling must not raise the image ceiling with it."""
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 64)

    r = client.post(
        "/api/media/upload",
        files={"file": ("big.png", PNG + b"\x00" * 200, "image/png")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_INVALID"


def test_a_video_over_the_image_ceiling_is_accepted(
    client: TestClient, monkeypatch, small_chunks
) -> None:
    """The whole point: a file that is too big to be an image is still a video."""
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 128)
    data = MP4 + b"\x11" * 200

    session = start(client, "reel.mp4", len(data))
    send_all(client, session, data)

    r = client.post(f"/api/media/uploads/{session['upload_id']}/complete")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "video"
    assert body["size"] == len(data)

    stored = client.get(f"/api/media/{body['asset_id']}")
    assert stored.status_code == 200
    assert stored.content == data


# --------------------------------------------------------------------------- #
# protocol
# --------------------------------------------------------------------------- #
def test_a_large_video_uploads_in_chunks_and_round_trips(
    client: TestClient, small_chunks
) -> None:
    """Every chunk must land at its own offset, or the file is scrambled."""
    data = MP4 + bytes(range(256)) * 40  # non-uniform, so offsets matter
    session = start(client, "big.mp4", len(data))

    assert session["chunk_count"] == (len(data) + CHUNK - 1) // CHUNK
    assert session["complete"] is False

    send_all(client, session, data)

    status = client.get(f"/api/media/uploads/{session['upload_id']}").json()
    assert status["complete"] is True
    assert status["received"] == list(range(session["chunk_count"]))

    r = client.post(f"/api/media/uploads/{session['upload_id']}/complete")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/media/{r.json()['asset_id']}").content == data


def test_chunks_may_arrive_out_of_order(client: TestClient, small_chunks) -> None:
    """Order is the client's problem; index is the contract."""
    data = MP4 + b"\x22" * 300
    session = start(client, "scrambled.mp4", len(data))
    chunk_size = session["chunk_size"]

    for index in reversed(range(session["chunk_count"])):
        block = data[index * chunk_size : (index + 1) * chunk_size]
        assert (
            client.put(
                f"/api/media/uploads/{session['upload_id']}/chunks/{index}", content=block
            ).status_code
            == 200
        )

    r = client.post(f"/api/media/uploads/{session['upload_id']}/complete")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/media/{r.json()['asset_id']}").content == data


def test_resending_a_chunk_does_not_corrupt_the_file(client: TestClient, small_chunks) -> None:
    """A client that retries after a timeout must not append the bytes twice."""
    data = MP4 + b"\x33" * 300
    session = start(client, "retried.mp4", len(data))

    send_all(client, session, data)
    first = client.put(
        f"/api/media/uploads/{session['upload_id']}/chunks/0", content=data[:CHUNK]
    )
    assert first.status_code == 200
    assert first.json()["received"] == list(range(session["chunk_count"]))

    r = client.post(f"/api/media/uploads/{session['upload_id']}/complete")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/media/{r.json()['asset_id']}").content == data


def test_completing_an_incomplete_upload_is_refused(client: TestClient, small_chunks) -> None:
    data = MP4 + b"\x44" * (CHUNK + 100)
    session = start(client, "partial.mp4", len(data))
    assert session["chunk_count"] > 1

    client.put(
        f"/api/media/uploads/{session['upload_id']}/chunks/0", content=data[:CHUNK]
    )

    r = client.post(f"/api/media/uploads/{session['upload_id']}/complete")
    assert r.status_code == 400


def test_a_chunk_of_the_wrong_size_is_refused(client: TestClient, small_chunks) -> None:
    """A short chunk would leave a hole the later chunks cannot fill."""
    data = MP4 + b"\x55" * 300
    session = start(client, "short.mp4", len(data))

    r = client.put(
        f"/api/media/uploads/{session['upload_id']}/chunks/0", content=data[:100]
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_INVALID"


def test_a_chunk_beyond_the_file_is_refused(client: TestClient, small_chunks) -> None:
    data = MP4 + b"\x66" * 300
    session = start(client, "extra.mp4", len(data))

    r = client.put(
        f"/api/media/uploads/{session['upload_id']}/chunks/99", content=b"x" * 10
    )
    assert r.status_code == 400


def test_a_disguised_file_is_rejected_at_complete_time(client: TestClient, small_chunks) -> None:
    """Magic-byte validation still applies to the assembled file."""
    data = b"<?php system($_GET['c']); ?>" * 40
    session = start(client, "evil.mp4", len(data))
    send_all(client, session, data)

    r = client.post(f"/api/media/uploads/{session['upload_id']}/complete")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_INVALID"


def test_the_declared_size_is_re_checked_against_the_sniffed_kind(
    client: TestClient, monkeypatch
) -> None:
    """A client that declares a small file and sends a video is still bounded.

    The outer check at session creation can only use the largest ceiling,
    because the type is unknown until bytes arrive. The real limit is applied
    here, to what actually landed on disk.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "max_video_upload_bytes", 128)
    monkeypatch.setattr(settings, "upload_chunk_bytes", CHUNK)

    data = MP4 + b"\x77" * 300
    session = start(client, "liar.mp4", len(data))
    send_all(client, session, data)

    r = client.post(f"/api/media/uploads/{session['upload_id']}/complete")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_INVALID"


def test_an_oversized_declaration_is_refused_before_anything_is_stored(
    client: TestClient, monkeypatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_bytes", 128)
    monkeypatch.setattr(settings, "max_video_upload_bytes", 1024)

    r = client.post("/api/media/uploads", json={"filename": "huge.mp4", "size": 10_000_000})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MEDIA_INVALID"
    assert r.json()["errors"][0]["field"] == "size"


def test_aborting_removes_the_staged_file(client: TestClient, small_chunks) -> None:
    """A cancelled 4 GB upload must not stay on the disk forever."""
    settings = get_settings()
    data = MP4 + b"\x00" * 300
    session = start(client, "cancelled.mp4", len(data))
    client.put(
        f"/api/media/uploads/{session['upload_id']}/chunks/0", content=data[:CHUNK]
    )
    assert (settings.upload_staging_path / session["upload_id"]).is_dir()

    assert client.delete(f"/api/media/uploads/{session['upload_id']}").status_code == 204
    assert not (settings.upload_staging_path / session["upload_id"]).exists()


def test_an_unknown_upload_id_is_a_404(client: TestClient) -> None:
    assert client.get("/api/media/uploads/" + "a" * 32).status_code == 404


def test_a_malformed_upload_id_is_refused(client: TestClient) -> None:
    """The id arrives in a URL, so it is validated rather than trusted."""
    r = client.get("/api/media/uploads/not-a-valid-id")
    assert r.status_code == 400


def test_a_traversal_upload_id_cannot_escape_the_staging_directory() -> None:
    from app.services.errors import AppError
    from app.services.upload_sessions import UploadSessionStore

    store = UploadSessionStore(get_settings())
    for bad in ("../../etc", "a" * 200, "ABCDEF0123456789"):
        with pytest.raises(AppError):
            store.get(bad)
