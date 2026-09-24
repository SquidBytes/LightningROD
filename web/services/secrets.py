"""Field-level encryption for credentials stored in the database.

Protects a leaked database file (backups, dumps, a copied SQLite file). It
does not protect against someone with access to the host, who can read the
key too.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from pathlib import Path
from typing import TypeGuard

from cryptography.fernet import Fernet, InvalidToken

from config import settings

logger = logging.getLogger("lightningrod.secrets")

PREFIX = "enc:v1:"
KEY_FILE_NAME = "secret.key"
# The Docker image declares /data as its persistent volume.
_DOCKER_DATA_DIR = Path("/data")


class SecretKeyUnavailable(RuntimeError):
    """No key could be read or created, so nothing can be encrypted."""


class SecretUnreadable(ValueError):
    """Ciphertext that the current key cannot decrypt — the key changed or was lost."""


def is_encrypted(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value.startswith(PREFIX)


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


def default_key_file() -> Path:
    if settings.secret_key_file is not None:
        return settings.secret_key_file
    if _DOCKER_DATA_DIR.is_dir() and os.access(_DOCKER_DATA_DIR, os.W_OK):
        return _DOCKER_DATA_DIR / KEY_FILE_NAME
    return Path("data") / KEY_FILE_NAME


def load_key_material(key_file: Path | None = None) -> str:
    """`LR_SECRET_KEY` if set, else the key file, created 0600 on first use."""
    if settings.secret_key:
        return settings.secret_key
    path = key_file or default_key_file()
    try:
        if path.exists():
            material = path.read_text().strip()
            if material:
                return material
        path.parent.mkdir(parents=True, exist_ok=True)
        material = secrets.token_urlsafe(32)
        # O_EXCL: two workers racing to create the key must not each write one.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(material + "\n")
        logger.info("Generated a new secret key at %s", path)
        return material
    except FileExistsError:
        return path.read_text().strip()
    except OSError as exc:
        raise SecretKeyUnavailable(f"cannot read or create key file {path}: {exc}") from exc


_box: SecretBox | None = None


def secret_box() -> SecretBox:
    """The process-wide box, resolving the key on first use."""
    global _box
    if _box is None:
        _box = SecretBox(load_key_material())
    return _box


def reset_secret_box() -> None:
    """Forget the cached key — tests and key-file changes use this."""
    global _box
    _box = None
