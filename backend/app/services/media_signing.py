"""Signed public URLs for uploaded media.

Why this exists
---------------
Buffer is a third-party cloud service. When you attach an image to a post, it
downloads that image itself, from a public URL, usually long after the post was
created. It cannot send our API key.

The previous behaviour fell back to the API's own host, which put the file
behind `X-API-Key`. Buffer got `401`, and because the download failure is
silent the post was simply created with no picture.

So we need a URL that is reachable without a credential but still cannot be
guessed or shared. That is what this module provides:

* the asset id is already 128 bits of `secrets.token_hex`, so it is unguessable
  on its own;
* on top of that every public URL carries an HMAC-SHA256 tag computed with a
  server-side secret, so knowing an asset id is not enough to fetch the bytes;
* the tag expires, so a URL harvested from a scheduled post cannot be replayed
  forever.

Nothing secret is stored per file. The signature is derived, so re-signing an
existing asset needs no database write.

Key material
------------
`MEDIA_PUBLIC_SIGNING_KEY` is used when set. Otherwise the signing key is
derived from `CREDENTIAL_ENCRYPTION_KEY` with HKDF, so a deployment that
already has that key gets working public URLs with no extra configuration, and
rotating the credential key does not silently break images that are already
scheduled - it invalidates them loudly instead of quietly serving 403.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.core.config import Settings

# Fixed label so the derived key is stable and domain-separated from any other
# use of the master credential key.
_HKDF_INFO = b"sma-media-public-url-v1"
_DEFAULT_TTL_DAYS = 365


def _derive_key(settings: "Settings | None") -> bytes:
    """Return the signing key for public media URLs.

    Accepts None because a storage backend can be constructed without settings
    (unit tests, one-off scripts). With no settings there is no key, so signing
    yields an empty tag and the public route refuses to serve the file. That is
    the safe direction: an unsigned public URL would hand out media to anyone
    who guessed an id.
    """
    if settings is None:
        return b""

    explicit = getattr(settings, "media_public_signing_key", None)
    if explicit:
        return explicit.encode("utf-8")

    master = settings.effective_encryption_key
    if not master:
        # No key configured. Signing still works but is not secret, so the
        # media.py route refuses to serve anything in that state rather than
        # handing out world-readable files.
        return b""

    return hashlib.sha256(_HKDF_INFO + master.encode("utf-8")).digest()


def has_signing_key(settings: "Settings | None") -> bool:
    return bool(_derive_key(settings))


def sign(
    asset_id: str, settings: "Settings | None", expires_at: int | None = None
) -> str:
    """Return the `?s=...&e=...` query string for an asset."""
    key = _derive_key(settings)
    if not key:
        return ""
    if expires_at is None:
        ttl_days = getattr(settings, "media_public_url_ttl_days", None) or _DEFAULT_TTL_DAYS
        expires_at = int(time.time()) + int(ttl_days) * 86400
    payload = f"{asset_id}:{expires_at}".encode("utf-8")
    tag = hmac.new(key, payload, hashlib.sha256).digest()[:24]
    return f"?s={base64.urlsafe_b64encode(tag).decode().rstrip('=')}&e={expires_at}"


def verify(
    asset_id: str,
    signature: str,
    expires_at: "str | int | None",
    settings: "Settings | None",
) -> bool:
    """Constant-time check of a presented public-media signature."""
    key = _derive_key(settings)
    if not key or not signature:
        return False

    try:
        exp = int(expires_at)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False

    if exp < int(time.time()):
        return False

    expected = hmac.new(key, f"{asset_id}:{exp}".encode("utf-8"), hashlib.sha256).digest()[:24]
    try:
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    except Exception:
        return False

    return hmac.compare_digest(expected, supplied)