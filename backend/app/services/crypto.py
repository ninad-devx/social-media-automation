"""Symmetric encryption for secrets at rest.

Provider API keys must never be stored in plain text, and must never reach the
browser. Encryption uses Fernet (AES-128-CBC + HMAC) from `cryptography`.

The key comes from `AI_ENCRYPTION_KEY` in the environment. If it is absent the
app runs in a clearly-marked degraded mode: secrets are NOT stored, provider
save attempts fail with an actionable message, and nothing silently falls back
to plain text.

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.logging import get_logger

logger = get_logger("app.crypto")

# Prefix makes an encrypted value recognisable and prevents us from ever
# treating a plaintext string as ciphertext.
PREFIX = "enc:v1:"


class EncryptionUnavailable(RuntimeError):
    """Raised when encryption is required but not configured."""


class SecretCipher:
    def __init__(self, key: str | None) -> None:
        self._fernet: Fernet | None = None
        if key and key.strip():
            try:
                self._fernet = Fernet(key.strip().encode("utf-8"))
            except (ValueError, TypeError) as exc:
                raise EncryptionUnavailable(
                    "AI_ENCRYPTION_KEY is not a valid Fernet key. "
                    "Generate one with: "
                    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
                ) from exc

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if not self._fernet:
            raise EncryptionUnavailable(
                "Cannot store the API key: AI_ENCRYPTION_KEY is not configured. "
                "Add it to backend/.env and restart."
            )
        return PREFIX + self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext or not ciphertext.startswith(PREFIX):
            # Refuse anything not produced by us rather than guessing.
            raise EncryptionUnavailable("Stored secret is not in the expected encrypted format.")
        if not self._fernet:
            raise EncryptionUnavailable(
                "Cannot decrypt the stored API key: AI_ENCRYPTION_KEY is not configured."
            )
        try:
            return self._fernet.decrypt(ciphertext[len(PREFIX) :].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            # Wrong key, or the value was tampered with. Never fall back.
            raise EncryptionUnavailable(
                "Could not decrypt the stored API key. AI_ENCRYPTION_KEY may have changed."
            ) from exc


def mask_secret(value: str | None) -> str | None:
    """Return a display-safe mask such as `sk-…f3a9`.

    The frontend only ever receives this, never the real key.
    """
    if not value:
        return None
    value = value.strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-4:]}"