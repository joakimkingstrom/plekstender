"""Encryption of secrets at rest (Plex tokens) and signing of short-lived values."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from plekstender.config import Settings

_VERSION = "v1"


class DecryptionError(Exception):
    pass


class TokenCipher:
    """AES-256-GCM encryption for small secrets.

    The `purpose` is bound as associated data, so a ciphertext stored in one column
    cannot be copied into another column and decrypted there.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("key must be 32 bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, *, purpose: str) -> str:
        nonce = os.urandom(12)
        ct = self._aead.encrypt(nonce, plaintext.encode(), purpose.encode())
        return f"{_VERSION}.{base64.urlsafe_b64encode(nonce + ct).decode()}"

    def decrypt(self, token: str, *, purpose: str) -> str:
        version, _, payload = token.partition(".")
        if version != _VERSION or not payload:
            raise DecryptionError("unknown ciphertext format")
        try:
            raw = base64.urlsafe_b64decode(payload)
            return self._aead.decrypt(raw[:12], raw[12:], purpose.encode()).decode()
        except (InvalidTag, ValueError) as exc:
            raise DecryptionError("could not decrypt value") from exc


class Signer:
    """HMAC signing for small values carried in cookies (e.g. a pending Plex PIN id)."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def _mac(self, message: str) -> str:
        digest = hmac.new(self._key, message.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def sign(self, value: str, *, now: float | None = None) -> str:
        issued = str(int(now if now is not None else time.time()))
        message = f"{value}.{issued}"
        return f"{message}.{self._mac(message)}"

    def unsign(self, signed: str, *, max_age: int, now: float | None = None) -> str | None:
        try:
            value, issued, mac = signed.rsplit(".", 2)
            issued_at = int(issued)
        except ValueError:
            return None
        if not hmac.compare_digest(mac, self._mac(f"{value}.{issued}")):
            return None
        current = now if now is not None else time.time()
        if current - issued_at > max_age or issued_at > current + 60:
            return None
        return value


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_cipher(settings: Settings) -> TokenCipher:
    """The cipher for secrets stored in the database (Plex tokens, API keys, scrobble
    tokens), keyed from APP_SECRET_KEY."""
    return TokenCipher(settings.derive_key("token-encryption"))
