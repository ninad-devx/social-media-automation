"""M6 + M7: S3 media resolution and module-level settings leakage."""
from __future__ import annotations

import io
import sys
import types

import pytest


class FakeS3Object:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class FakeS3Client:
    """A minimal, in-memory stand-in for boto3's S3 client.

    Multipart is modelled because the production path uses it: anything past
    `_S3_SINGLE_PUT_LIMIT` is streamed in parts, so a fake without these
    methods fails on every large upload.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.uploads: dict[str, dict] = {}
        self.aborted: list[str] = []

    def put_object(self, *, Bucket, Key, Body, ContentType, CacheControl):  # noqa: N803
        data = Body if isinstance(Body, bytes) else Body.read()
        self.objects[Key] = data
        self.content_types[Key] = ContentType

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise KeyError(Key)
        body = self.objects[Key]
        return {"Body": FakeS3Object(body), "ContentLength": len(body)}

    def delete_object(self, *, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)

    # -- multipart ------------------------------------------------------- #
    def create_multipart_upload(self, *, Bucket, Key, ContentType, CacheControl):  # noqa: N803
        upload_id = f"mpu-{len(self.uploads) + 1}"
        self.uploads[upload_id] = {"key": Key, "parts": {}, "content_type": ContentType}
        return {"UploadId": upload_id}

    def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):  # noqa: N803
        data = Body if isinstance(Body, bytes) else Body.read()
        self.uploads[UploadId]["parts"][PartNumber] = data
        return {"ETag": f'"etag-{PartNumber}"'}

    def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload):  # noqa: N803
        meta = self.uploads.pop(UploadId)
        parts = sorted(p["PartNumber"] for p in MultipartUpload["Parts"])
        self.objects[Key] = b"".join(meta["parts"][n] for n in parts)
        self.content_types[Key] = meta["content_type"]
        return {}

    def abort_multipart_upload(self, *, Bucket, Key, UploadId):  # noqa: N803
        self.uploads.pop(UploadId, None)
        self.aborted.append(UploadId)
        return {}


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def s3_storage(monkeypatch):
    """An S3ObjectStorage whose boto client is the in-memory fake."""
    from app.services import storage as storage_module

    fake = FakeS3Client()
    monkeypatch.setattr(storage_module, "_s3_client", lambda *args, **kwargs: fake)
    return fake, storage_module


def build_storage(storage_module, tmp_path):
    return storage_module.S3ObjectStorage(
        bucket="test-bucket",
        endpoint="https://example.invalid",
        access_key="k",
        secret_key="s",
        public_base="https://cdn.example.invalid",
        region="auto",
    )


def test_saved_object_round_trips_and_can_be_deleted(s3_storage, tmp_path):
    fake, storage_module = s3_storage
    store = build_storage(storage_module, tmp_path)
    stored = store.save(
        io.BytesIO(PNG), storage_module.MediaKind("image", "image/png", "png"), "a.png", len(PNG)
    )
    assert stored.public_url.startswith("https://cdn.example.invalid/")
    assert stored.asset_id in fake.objects

    handle = store.open(stored.asset_id)
    assert handle is not None
    stream, kind, size = handle
    assert stream.read() == PNG
    assert kind.mime == "image/png"
    assert size == len(PNG)

    assert store.delete(stored.asset_id) is True
    assert stored.asset_id not in fake.objects


def test_missing_object_returns_none(s3_storage, tmp_path):
    fake, storage_module = s3_storage
    store = build_storage(storage_module, tmp_path)
    assert store.open("nothing.png") is None
    # S3 DELETE is idempotent and reports success for an absent key, so True
    # here is the correct AWS semantic rather than a bug.
    assert store.delete("nothing.png") is True


def test_resolve_is_not_used_for_remote_objects(s3_storage, tmp_path):
    """A remote object has no local path.

    This is the M6 trap: `resolve()` returning None is correct for S3, but the
    two media GET routes treated it as "not found" and 404'd on every request.
    They now use `open()`, which is the S3-aware path.
    """
    fake, storage_module = s3_storage
    store = build_storage(storage_module, tmp_path)
    stored = store.save(
        io.BytesIO(PNG), storage_module.MediaKind("image", "image/png", "png"), "a.png", len(PNG)
    )
    assert store.resolve(stored.asset_id) is None
    # The object is nevertheless reachable through open().
    assert store.open(stored.asset_id) is not None


def test_the_media_routes_serve_an_s3_object(tmp_path, monkeypatch):
    """End-to-end through the API with a mocked S3 client."""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.services import storage as storage_module

    fake = FakeS3Client()
    monkeypatch.setattr(storage_module, "_s3_client", lambda *args, **kwargs: fake)
    monkeypatch.setattr(get_settings(), "media_storage_backend", "s3")
    monkeypatch.setattr(get_settings(), "s3_bucket", "test-bucket")
    monkeypatch.setattr(get_settings(), "s3_endpoint", "https://example.invalid")
    monkeypatch.setattr(get_settings(), "s3_public_base_url", "https://cdn.example.invalid")

    from app.main import app

    with TestClient(app) as client:
        uploaded = client.post(
            "/api/media/upload",
            files={"file": ("a.png", PNG, "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset_id = uploaded.json()["asset_id"]
        assert asset_id in fake.objects

        served = client.get(f"/api/media/{asset_id}")
        assert served.status_code == 200, served.text
        assert served.content == PNG


def test_signed_url_generation_uses_injected_settings(tmp_path, monkeypatch):
    """M7: the module-level `get_settings()` must not leak into URL signing.

    `_settings = get_settings()` at import time meant the signing key and the
    storage backend could disagree when settings were overridden at runtime.
    """
    from app.core.config import Settings
    from app.services import storage as storage_module
    from app.services import media_signing

    settings = Settings(
        _env_file=None,
        media_storage_dir=str(tmp_path),
        public_api_base_url="https://api.example.invalid",
        media_public_signing_key="a-test-signing-key",
    )

    store = storage_module.LocalDiskStorage(
        root=settings.media_path,
        base_url=settings.media_base_url,
        publicly_reachable=True,
        settings=settings,
    )
    url = store.signed_public_url("abc123")
    assert url.startswith("https://api.example.invalid/api/media/abc123?")
    # It must verify against the same key that signed it.
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(url).query)
    assert media_signing.verify(
        "abc123", query["s"][0], int(query["e"][0]), settings
    )


def test_module_level_settings_is_not_used_for_signing():
    from app.services import storage as storage_module

    # The constant must be gone, or at least not on the signing path.
    assert not hasattr(storage_module, "_settings"), (
        "storage.py still holds a module-level settings snapshot; pass settings "
        "through the storage object instead"
    )


# Keep the unused imports meaningful for linters.
assert io and sys and types and pytest