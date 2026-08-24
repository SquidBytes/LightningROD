"""Verbatim archive of incoming Home Assistant events.

Typed ingestion maps only the attributes it has contracts for; everything
else is dropped and is recoverable afterwards only from Home Assistant's own
recorder retention. The archive keeps the whole event payload so the data
stays re-derivable locally.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from db.engine import AsyncSessionLocal
from db.models.raw_event import HARawEvent
from db.portable_insert import portable_insert
from web.queries.settings import get_raw_archive_settings
from web.services.ingestion._helpers import _get_event_timestamp
from web.services.sources.ha_fordpass.adapter import INGEST_SCHEMA_VERSION
from web.services.sources.ha_fordpass.handlers import extract_slug, get_device_id

logger = logging.getLogger("lightningrod.ingestion.raw_archive")

SOURCE_SYSTEM = "ha_fordpass"
SETTINGS_TTL = 60.0  # seconds


def _utc(ts: datetime | None) -> datetime | None:
    """UTC-normalized copy. SQLite drops tzinfo on bind, so offsets must resolve first."""
    if ts is None:
        return None
    return ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)


class RawEventArchive:
    """Writes each fordpass event to `ha_raw_events` before typed mapping runs."""

    def __init__(self) -> None:
        self._settings: dict | None = None
        self._settings_at: float = 0.0

    async def store(self, entity_id: str, new_state: dict, *, config_id: int) -> None:
        """Archive one raw event. Swallows every failure — ingestion must not stop."""
        if not entity_id or not new_state:
            return
        slug = extract_slug(entity_id)
        if slug is None:
            return
        try:
            async with AsyncSessionLocal() as db:
                settings = await self._settings_for(db)
                if not settings["enabled"]:
                    return
                await self._insert(db, entity_id, slug, new_state, config_id)
                await db.commit()
        except Exception:
            logger.exception("raw archive write failed for %s", entity_id)

    def invalidate_settings(self) -> None:
        """Drop the cached settings so the next event re-reads them."""
        self._settings = None

    def reset(self) -> None:
        """Clear all instance state — test fixtures call this between tests."""
        self._settings = None
        self._settings_at = 0.0

    async def _settings_for(self, db) -> dict:
        """Archive settings, cached for SETTINGS_TTL so events don't each read them."""
        now = time.monotonic()
        if self._settings is not None and now - self._settings_at < SETTINGS_TTL:
            return self._settings
        self._settings = await get_raw_archive_settings(db)
        self._settings_at = now
        return self._settings

    async def _insert(
        self, db, entity_id: str, slug: str, new_state: dict, config_id: int
    ) -> None:
        state = new_state.get("state")
        stmt = portable_insert(HARawEvent, dialect=db.bind.dialect).values(
            entity_id=entity_id,
            device_id=get_device_id(entity_id, {}),
            slug=slug,
            state=None if state is None else str(state),
            payload=new_state,
            recorded_at=_utc(_get_event_timestamp(new_state)) or datetime.now(UTC),
            config_id=config_id,
            source_system=SOURCE_SYSTEM,
            ingest_schema_version=INGEST_SCHEMA_VERSION,
        )
        # A reconnect replays the initial snapshot with its original
        # timestamps, so the same event arrives more than once.
        await db.execute(
            stmt.on_conflict_do_nothing(index_elements=["entity_id", "recorded_at"])
        )


raw_archive = RawEventArchive()
