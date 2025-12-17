"""Encrypted storage for provider credentials entered through the UI.

Secrets are encrypted with AES-GCM before they touch the database. The
encryption key comes from ``APP_SECRET_KEY``; when that is not set a random key
is generated once and stored in a 0600 file under the data directory.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

from app.config import resolve_secret_key

try:  # pragma: no cover - exercised implicitly by the import
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    _CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover - fallback for restricted environments
    AESGCM = None  # type: ignore[assignment,misc]
    _CRYPTO_AVAILABLE = False


class SecretError(RuntimeError):
    """Raised when a secret cannot be encrypted or decrypted."""


def _key() -> bytes:
    return hashlib.sha256(resolve_secret_key().encode("utf-8")).digest()


def is_available() -> bool:
    return _CRYPTO_AVAILABLE


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string, returning a self-describing base64 envelope."""
    if not _CRYPTO_AVAILABLE:  # pragma: no cover - depends on environment
        raise SecretError("The 'cryptography' package is required to store provider credentials.")
    nonce = os.urandom(12)
    cipher = AESGCM(_key())
    ciphertext = cipher.encrypt(nonce, plaintext.encode("utf-8"), b"tracelens-secret-v1")
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt(envelope: str) -> str:
    if not _CRYPTO_AVAILABLE:  # pragma: no cover - depends on environment
        raise SecretError("The 'cryptography' package is required to read provider credentials.")
    try:
        raw = base64.urlsafe_b64decode(envelope.encode("ascii"))
    except Exception as exc:
        raise SecretError("Stored credential could not be decoded.") from exc
    if len(raw) <= 12:
        raise SecretError("Stored credential is malformed.")
    nonce, ciphertext = raw[:12], raw[12:]
    try:
        plaintext = AESGCM(_key()).decrypt(nonce, ciphertext, b"tracelens-secret-v1")
    except Exception as exc:
        raise SecretError("Stored credential could not be decrypted with the configured secret key.") from exc
    return plaintext.decode("utf-8")


def mask(value: str | None) -> str:
    """Return a non-reversible hint that shows whether a value is present."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-2:]}"


def safe_repr(value: Any) -> str:
    return mask(value if isinstance(value, str) else None)
