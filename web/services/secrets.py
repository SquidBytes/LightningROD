"""Optional field-level encryption for credentials stored in the database.

Encryption is on only when a key is configured (`LR_SECRET_KEY`, or
`LR_SECRET_KEY_FILE` naming an existing file such as a Docker secret). No
key is ever generated. Without one, credentials are stored as plain text.

It protects a leaked database file (backups, dumps, a copied SQLite file),
not a compromised host, which can read the key too.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import TypeGuard

from cryptography.fernet import Fernet, InvalidToken

from config import settings

logger = logging.getLogger("lightningrod.secrets")

PREFIX = "enc:v1:"


class SecretUnreadable(ValueError):
    """Ciphertext that cannot be decrypted — the key is missing or changed."""


def is_encrypted(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value.startswith(PREFIX)


def mask_secret(value: str) -> str:
    """Asterisks plus the last 8 characters — enough to tell two secrets apart."""
    if not value:
        return ""
    if len(value) > 8:
        return "*" * (len(value) - 8) + value[-8:]
    return value


class SecretBox:
    """Encrypts strings into `enc:v1:` values and back."""

    def __init__(self, key_material: str) -> None:
        # Any string works as key material (a Docker secret, a passphrase):
        # hash it to the 32 bytes Fernet needs rather than demand its format.
        digest = hashlib.sha256(key_material.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        if is_encrypted(plaintext):
            return plaintext
        return PREFIX + self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, value: str) -> str:
        """Plaintext for an `enc:v1:` value; anything unprefixed passes through."""
        if not is_encrypted(value):
            return value
        try:
            return self._fernet.decrypt(value[len(PREFIX) :].encode()).decode()
        except InvalidToken as exc:
            raise SecretUnreadable("stored secret does not match the current key") from exc


def load_key_material() -> str | None:
    """The configured key, or None when encryption is off."""
    if settings.secret_key:
        return settings.secret_key
    path = settings.secret_key_file
    if path is None:
        return None
    try:
        material = path.read_text().strip()
    except OSError:
        logger.error("LR_SECRET_KEY_FILE %s cannot be read; encryption is off", path)
        return None
    return material or None


# Empty until first use; then holds the box, or None when encryption is off.
_cache: list[SecretBox | None] = []


def secret_box() -> SecretBox | None:
    """The process-wide box, or None when no key is configured."""
    if not _cache:
        material = load_key_material()
        _cache.append(SecretBox(material) if material else None)
    return _cache[0]


def encryption_enabled() -> bool:
    return secret_box() is not None


def reset_secret_box() -> None:
    """Forget the cached key — tests and key changes use this."""
    _cache.clear()
