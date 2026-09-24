"""Encrypt and decrypt the secret keys of a `data_source_configs.config_json`.

Every read and write of `config_json` goes through here, so a source runtime
only ever sees plaintext and the row only ever holds ciphertext. Which keys
are secret is each source's own `DataSource.secret_fields` declaration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.data_source_config import DataSourceConfig
from web.services.secrets import SecretUnreadable, is_encrypted, secret_box
from web.services.sources.registry import REGISTRY

logger = logging.getLogger("lightningrod.sources.config_secrets")


def secret_fields_for(source_name: str) -> frozenset[str]:
    for source in REGISTRY:
        if source.source_name == source_name:
            return source.secret_fields
    return frozenset()


@dataclass(frozen=True)
class OpenedConfig:
    """Decrypted config, minus any secret the current key could not read."""

    values: dict
    unreadable: frozenset[str] = frozenset()


def seal_config(source_name: str, config: dict | None) -> dict:
    """Copy of `config` with every declared secret encrypted."""
    sealed = dict(config or {})
    fields = secret_fields_for(source_name)
    if not fields:
        return sealed
    box = secret_box()
    for key in fields:
        value = sealed.get(key)
        if isinstance(value, str) and value:
            sealed[key] = box.encrypt(value)
    return sealed


def open_config(source_name: str, config: dict | None) -> OpenedConfig:
    """Decrypt declared secrets; ones the key cannot read are dropped and named."""
    opened = dict(config or {})
    fields = secret_fields_for(source_name)
    if not any(is_encrypted(opened.get(key)) for key in fields):
        return OpenedConfig(opened)
    box = secret_box()
    unreadable: set[str] = set()
    for key in fields:
        value = opened.get(key)
        if not is_encrypted(value):
            continue
        try:
            opened[key] = box.decrypt(value)
        except SecretUnreadable:
            opened.pop(key)
            unreadable.add(key)
    return OpenedConfig(opened, frozenset(unreadable))


def needs_sealing(source_name: str, config: dict | None) -> bool:
    fields = secret_fields_for(source_name)
    return any(
        isinstance(v, str) and v and not is_encrypted(v)
        for v in ((config or {}).get(k) for k in fields)
    )


async def seal_stored_configs(db: AsyncSession) -> int:
    """Encrypt any plaintext secret left in a config row; returns rows changed.

    Runs on every start rather than as a migration because migrations have no
    access to the key. Idempotent: already-sealed values are left alone.
    """
    rows = (await db.execute(select(DataSourceConfig))).scalars().all()
    changed = 0
    for row in rows:
        if needs_sealing(row.source_name, row.config_json):
            row.config_json = seal_config(row.source_name, row.config_json)
            changed += 1
    if changed:
        await db.commit()
        logger.info("Encrypted stored credentials in %d config row(s)", changed)
    return changed
