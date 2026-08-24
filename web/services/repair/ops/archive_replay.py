"""Archive replay: re-drive locally archived Home Assistant events through ingestion."""

from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import func, select

from db.models.raw_event import HARawEvent
from web.services.repair.base import _aware
from web.services.repair.recorder_replay import _WINDOW_CACHE_TTL, RecorderReplay


class _ArchiveSource:
    """Stands in for the HA runtime — replay only reads `_ha_config` off it."""

    def __init__(self, ha_config: dict, detected_vin: str | None = None):
        self._ha_config = ha_config
        self.detected_vin = detected_vin


class ArchiveReplay(RecorderReplay):
    """Re-derive trips from LightningROD's own raw event archive.

    Same replay machinery as the recorder op — deterministic trip ids, the
    preservation revert, per-field fill counts — but the states come from
    `ha_raw_events` instead of Home Assistant, so it works offline and is not
    bounded by the recorder's retention. Archiving happens above the slug
    dispatcher, so a replay can never feed itself.
    """

    slug = "archive-replay"
    display_name = "Event archive replay"
    description = (
        "Replays trip-related events from LightningROD's own event archive "
        "through the ingestion pipeline, filling missing trip fields and "
        "recovering trips that were never ingested. No Home Assistant "
        "connection needed."
    )
    runs_when_clean = True

    def __init__(self, runtime=None, session_factory=None):
        super().__init__(runtime)
        self._session_factory = session_factory

    def _sessions(self):
        """Session factory for archive reads; overridden in tests."""
        if self._session_factory is not None:
            return self._session_factory
        from db.engine import AsyncSessionLocal

        return AsyncSessionLocal

    def _get_runtime(self):
        """The live runtime if there is one, else a config-only stand-in."""
        if self._runtime is not None:
            return self._runtime
        from web.services.ingestion.supervisor import supervisor

        live = supervisor.get_runtime("ha_fordpass", "default")
        return _ArchiveSource(
            getattr(live, "_ha_config", None) or {},
            getattr(live, "detected_vin", None),
        )

    def ha_connected(self) -> bool:
        """Always true — the archive is local data."""
        return True

    async def recorder_window(self) -> datetime | None:
        """Earliest archived trip-events timestamp; cached like the recorder probe."""
        now = time.monotonic()
        if (
            self._window_probed_at is not None
            and now - self._window_probed_at < _WINDOW_CACHE_TTL
        ):
            return self._window
        async with self._sessions()() as db:
            earliest = await db.scalar(
                select(func.min(HARawEvent.recorded_at)).where(
                    HARawEvent.slug == "events"
                )
            )
        self._window = _aware(earliest)
        self._window_probed_at = now
        return self._window

    async def _fetch_states(self) -> list[tuple[datetime, str, dict]]:
        """Archived trip-entity payloads in the window, ascending by event time."""
        window = await self.recorder_window()
        if window is None:
            return []
        async with self._sessions()() as db:
            stmt = (
                select(HARawEvent)
                .where(
                    HARawEvent.slug.in_(self.TRIP_ENTITY_SUFFIXES),
                    HARawEvent.recorded_at >= window,
                )
                .order_by(HARawEvent.recorded_at, HARawEvent.id)
            )
            rows = list((await db.execute(stmt)).scalars().all())
        return [(_aware(row.recorded_at), row.entity_id, row.payload) for row in rows]
