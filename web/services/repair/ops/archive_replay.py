"""Archive replay: re-drive locally archived Home Assistant events through ingestion."""

from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import func, select

from db.models.raw_event import HARawEvent
from web.services.repair.base import _aware
from web.services.repair.recorder_replay import _WINDOW_CACHE_TTL, RecorderReplay


class _ArchiveSource:
    """Stands in for the HA runtime — replay only reads `_ha_config` off it.

    Resolved late, through the operation, because replay snapshots the config
    after probing the window, which is where the archived unit system loads.
    """

    def __init__(self, op: ArchiveReplay):
        self._op = op

    @property
    def _ha_config(self) -> dict:
        return self._op.replay_ha_config()


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
        self._unit_system: dict | str | None = None

    def _sessions(self):
        """Session factory for archive reads; overridden in tests."""
        if self._session_factory is not None:
            return self._session_factory
        from db.engine import AsyncSessionLocal

        return AsyncSessionLocal

    def _get_runtime(self):
        """The injected runtime if there is one, else a config-only stand-in."""
        if self._runtime is not None:
            return self._runtime
        return _ArchiveSource(self)

    def replay_ha_config(self) -> dict:
        """Config the archived payloads must be read under.

        ha-fordpass localizes trip distances and temperatures into Home
        Assistant's unit system before emitting them, so replaying an
        imperial payload without that unit system stores miles as kilometres
        and °F as °C. The archived value wins: it is the system that was in
        force when the payload was captured. A live connection is only a
        fallback for rows captured before the unit system was recorded.
        """
        if self._unit_system is not None:
            return {"unit_system": self._unit_system}
        from web.services.ingestion.supervisor import supervisor

        live = supervisor.get_runtime("ha_fordpass", "default")
        return dict(getattr(live, "_ha_config", None) or {})

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
            # Loaded on the same probe: replay reads the config once, right
            # after this call, so it has to be in place by the time it does.
            self._unit_system = await db.scalar(
                select(HARawEvent.ha_unit_system)
                .where(HARawEvent.ha_unit_system.isnot(None))
                .order_by(HARawEvent.recorded_at.desc())
                .limit(1)
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
