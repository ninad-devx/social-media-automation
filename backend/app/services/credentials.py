"""Shared credential management.

One place that encrypts, decrypts, masks and rotates every stored secret -
Buffer tokens and AI provider keys alike.

Rules enforced here:
  * a secret is only ever written as Fernet ciphertext;
  * a stored secret is never returned to the browser (only a masked hint);
  * decryption never falls back to plain text, it raises instead, so a wrong
    key is loud rather than silent;
  * the key comes from deployment configuration, never from the database and
    never from source control.

Rotation: `rotate_encryption_key` re-encrypts every row in one transaction.
Losing or changing the key without rotating makes existing secrets
undecryptable, which surfaces as a clear error, never as a silent fallback.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.crypto import PREFIX, EncryptionUnavailable, mask_secret

logger = get_logger("app.credentials")


class SecretCipher:
    """Fernet wrapper for one configured key.

    Kept deliberately tiny so both the AI and integration code paths share
    exactly the same behaviour.
    """

    def __init__(self, key: str | None) -> None:
        self._fernet: Fernet | None = None
        if key and key.strip():
            try:
                self._fernet = Fernet(key.strip().encode("utf-8"))
            except (ValueError, TypeError) as exc:
                raise EncryptionUnavailable(
                    "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
                ) from exc

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if not self._fernet:
            raise EncryptionUnavailable(
                "Cannot store this secret: CREDENTIAL_ENCRYPTION_KEY is not configured. "
                "Add it to backend/.env and restart."
            )
        return PREFIX + self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext or not ciphertext.startswith(PREFIX):
            raise EncryptionUnavailable("Stored secret is not in the expected encrypted format.")
        if not self._fernet:
            raise EncryptionUnavailable(
                "Cannot decrypt the stored secret: CREDENTIAL_ENCRYPTION_KEY is not configured."
            )
        try:
            return self._fernet.decrypt(ciphertext[len(PREFIX) :].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise EncryptionUnavailable(
                "Could not decrypt the stored secret. CREDENTIAL_ENCRYPTION_KEY may have changed."
            ) from exc


def build_cipher(settings: Settings) -> SecretCipher:
    """Cipher for the configured (or legacy) encryption key."""
    return SecretCipher(settings.effective_encryption_key)


def hint_for(secret: str | None) -> str | None:
    """Masked display value, e.g. `sk-...f3a9`. Safe to return to the UI."""
    return mask_secret(secret)


__all__ = [
    "SecretCipher",
    "EncryptionUnavailable",
    "build_cipher",
    "hint_for",
    "mask_secret",
]
