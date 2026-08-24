"""Verbatim archive of incoming Home Assistant events.

Typed ingestion maps only the attributes it has contracts for; everything
else is dropped and is recoverable afterwards only from Home Assistant's own
recorder retention. The archive keeps the whole event payload so the data
stays re-derivable locally.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from db.engine import AsyncSessionLocal
from db.models.raw_event import HARawEvent
from db.portable_insert import portable_insert
from web.queries.settings import get_raw_archive_settings
from web.services.ingestion._helpers import _get_event_timestamp
from web.services.sources.ha_fordpass.adapter import INGEST_SCHEMA_VERSION
from web.services.sources.ha_fordpass.handlers import extract_slug, get_device_id

logger = logging.getLogger("lightningrod.ingestion.raw_archive")

SOURCE_SYSTEM = "ha_fordpass"
# Home Assistant freezes last_changed while only attributes change, so the
# archive keys off last_updated: it advances on every update, which keeps
# attribute-only events distinct, and a reconnect snapshot re-emits the same
# value, which is what the dedup index is for.
EVENT_TS_KEYS = ("last_updated", "last_changed")
SETTINGS_TTL = 60.0  # seconds
PRUNE_INTERVAL = 86400.0  # one retention pass a day is plenty
PRUNE_BACKLOG_INTERVAL = 60.0  # ...unless a backlog is still draining
PRUNE_BATCH = 5000


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
        self._prune_due_at: float = 0.0

    async def store(
        self,
        entity_id: str,
        new_state: dict,
        *,
        config_id: int,
        ha_config: dict | None = None,
    ) -> None:
        """Archive one raw event.

        Everything runs inside the guard: this is called from the ingestion
        fan-out, where an escaping exception would stop typed writes too.
        """
        try:
            if not entity_id or not new_state:
                return
            slug = extract_slug(entity_id)
            if slug is None:
                return
            async with AsyncSessionLocal() as db:
                settings = await self._settings_for(db)
                if settings["enabled"]:
                    await self._insert(
                        db, entity_id, slug, new_state, config_id, ha_config
                    )
                    await db.commit()
            # Retention keeps running with archiving switched off: turning it
            # off to reclaim disk must not freeze what is already stored.
            await self._maybe_prune(settings["retention_days"])
        except Exception:
            logger.exception("raw archive failed for %s", entity_id)

    def invalidate_settings(self) -> None:
        """Drop the cached settings so the next event re-reads them."""
        self._settings = None

    def reset(self) -> None:
        """Clear all instance state — test fixtures call this between tests."""
        self._settings = None
        self._settings_at = 0.0
        self._prune_due_at = 0.0

    async def _settings_for(self, db) -> dict:
        """Archive settings, cached for SETTINGS_TTL so events don't each read them."""
        now = time.monotonic()
        if self._settings is not None and now - self._settings_at < SETTINGS_TTL:
            return self._settings
        self._settings = await get_raw_archive_settings(db)
        self._settings_at = now
        return self._settings

    async def _insert(
        self,
        db,
        entity_id: str,
        slug: str,
        new_state: dict,
        config_id: int,
        ha_config: dict | None,
    ) -> None:
        state = new_state.get("state")
        stmt = portable_insert(HARawEvent, dialect=db.bind.dialect).values(
            entity_id=entity_id,
            device_id=get_device_id(entity_id, {}),
            slug=slug,
            state=None if state is None else str(state),
            payload=new_state,
            # Only the unit system, not the whole HA config: the rest of it is
            # instance metadata (home coordinates, location name) the archive
            # has no reason to hold.
            ha_unit_system=(ha_config or {}).get("unit_system"),
            recorded_at=(
                _utc(_get_event_timestamp(new_state, EVENT_TS_KEYS))
                or datetime.now(UTC)
            ),
            config_id=config_id,
            source_system=SOURCE_SYSTEM,
            ingest_schema_version=INGEST_SCHEMA_VERSION,
        )
        # Suppresses only true re-sends: a reconnect replays the initial
        # snapshot with the original last_updated of every entity.
        await db.execute(
            stmt.on_conflict_do_nothing(index_elements=["entity_id", "recorded_at"])
        )

    async def _maybe_prune(self, retention_days: int) -> None:
        """Drop one bounded batch of expired rows, at most once per throttle tick."""
        if retention_days <= 0:  # keep forever
            return
        now = time.monotonic()
        if now < self._prune_due_at:
            return
        deleted = 0
        try:
            deleted = await self._prune_expired(retention_days)
        except Exception:
            logger.exception("raw archive prune failed")
        finally:
            # Re-arm even after a failure, so one bad retention value cannot
            # make every subsequent event retry the same broken delete. A full
            # batch means more is waiting — come back in a minute, not a day.
            self._prune_due_at = now + (
                PRUNE_BACKLOG_INTERVAL if deleted >= PRUNE_BATCH else PRUNE_INTERVAL
            )

    async def _prune_expired(self, retention_days: int) -> int:
        """Delete up to PRUNE_BATCH rows that fell out of the retention window."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with AsyncSessionLocal() as db:
            # This runs on the ingestion path, so the delete has to be
            # bounded. PostgreSQL has no DELETE ... LIMIT and SQLite's is
            # compile-time optional, hence the id subquery.
            expired = (
                select(HARawEvent.id)
                .where(HARawEvent.recorded_at < cutoff)
                .order_by(HARawEvent.id)
                .limit(PRUNE_BATCH)
            )
            result = await db.execute(
                delete(HARawEvent).where(HARawEvent.id.in_(expired))
            )
            await db.commit()
        return int(getattr(result, "rowcount", 0) or 0)


raw_archive = RawEventArchive()
