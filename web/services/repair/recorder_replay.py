"""Recorder replay: re-drive HA recorder history through ingestion to fix trips."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.services.repair.base import (
    MUTABLE_SOURCE_SYSTEMS,
    RepairDiff,
    RepairOperation,
    RepairResult,
    mutable_only,
    rollback_session,
)
from web.services.repair.snapshot import deserialize_row, serialize_row

logger = logging.getLogger("lightningrod.repair.recorder_replay")

# Pending-state caches in handlers.py are keyed (config_id, device_id).
# Replay uses a sentinel config_id so its pending entries can never collide
# with (or bleed into) the live runtime's keys; leftovers are purged post-run.
SENTINEL_CONFIG_ID = -1

_WINDOW_CACHE_TTL = 300.0  # seconds


def _state_ts(state: dict) -> datetime | None:
    """UTC-aware timestamp of an HA history state (last_updated/last_changed)."""
    for key in ("last_updated", "last_changed"):
        val = state.get(key)
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=UTC)
        if isinstance(val, str) and val:
            try:
                parsed = datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


class RecorderReplay(RepairOperation):
    """Replay trip-entity history from HA's recorder through live ingestion.

    Replay touches ONLY ev_trip_metrics: metrics states route straight to
    the trip regen backfill (never the battery-status writer) and elveh
    pending state is purged unflushed, so no EVBatteryStatus/EVVehicleStatus
    rows are ever written. Battery/vehicle status history recovery is
    explicitly out of scope — the snapshot/restore model doesn't cover it.

    Apply-session semantics: `handle_battery_status` commits mid-replay on
    its elveh trip-creation branch, so an apply persists the snapshot plus
    partial progress together. Safe because the snapshot is taken first and
    replay is idempotent — a rerun converges to the same state. Dry-run
    neutralizes those commits via `rollback_session` savepoints.

    Dedup safety: deterministic trip_ids (uuid5 of device|tripUpdateTime)
    plus the `around=event-ts` predicate match mean replayed states enrich
    existing rows rather than duplicating them.
    """

    slug = "recorder-replay"
    display_name = "Recorder history replay"
    description = (
        "Replays trip-related sensor history from Home Assistant's recorder "
        "through the ingestion pipeline, filling missing trip fields and "
        "recovering trips that were never ingested."
    )
    model = EVTripMetrics

    TRIP_ENTITY_SUFFIXES = ("events", "metrics", "elveh")
    ENRICHABLE_FIELDS = (
        "duration",
        "start_time",
        "odometer_start",
        "odometer_end",
        "range_regenerated",
        "driving_score",
        "speed_score",
        "acceleration_score",
        "deceleration_score",
        "ambient_temp",
        "cabin_temp",
        "outside_air_temp",
    )

    def __init__(self, runtime=None):
        self._runtime = runtime
        self._window: datetime | None = None
        self._window_probed_at: float | None = None
        # Test seam: engine handed to preview()'s rollback_session
        # (None = the app engine).
        self._rollback_engine = None
        # Detail counts from the most recent execute(), for route rendering.
        self.last_details: dict[str, Any] = {}

    def _get_runtime(self):
        """Injected runtime, else the supervisor's ha_fordpass/default one."""
        if self._runtime is not None:
            return self._runtime
        # Lazy import: registry import must never pull in the HA stack.
        from web.services.ingestion.supervisor import supervisor

        return supervisor.get_runtime("ha_fordpass", "default")

    def _entity_id(self, runtime, suffix: str) -> str:
        return f"sensor.fordpass_{runtime.detected_vin or 'unknown'}_{suffix}"

    # ------------------------------------------------------------------
    # Recorder window
    # ------------------------------------------------------------------

    def ha_connected(self) -> bool:
        """True when the HA runtime exists and reports connected."""
        runtime = self._get_runtime()
        return runtime is not None and bool(runtime.health.get("connected"))

    async def recorder_window(self) -> datetime | None:
        """Earliest events-entity timestamp in the recorder; cached ~5 min."""
        runtime = self._get_runtime()
        if runtime is None or not runtime.health.get("connected"):
            return None
        now = time.monotonic()
        if (
            self._window_probed_at is not None
            and now - self._window_probed_at < _WINDOW_CACHE_TTL
        ):
            return self._window
        entity_id = self._entity_id(runtime, "events")
        states = await runtime._fetch_entity_history(entity_id)
        timestamps = [ts for ts in map(_state_ts, states) if ts is not None]
        self._window = min(timestamps) if timestamps else None
        if self._window is None:
            logger.info(
                "recorder window probe found no history for %s — "
                "check HA recorder retention and that the entity exists",
                entity_id,
            )
        self._window_probed_at = now
        return self._window

    # ------------------------------------------------------------------
    # Census / affected rows
    # ------------------------------------------------------------------

    def _census_filters(self, window: datetime):
        return (
            mutable_only(EVTripMetrics),
            EVTripMetrics.end_time >= window,
            or_(*(getattr(EVTripMetrics, f).is_(None) for f in self.ENRICHABLE_FIELDS)),
        )

    async def census(self, db: AsyncSession) -> int:
        window = await self.recorder_window()
        if window is None:
            return 0
        stmt = (
            select(func.count())
            .select_from(EVTripMetrics)
            .where(*self._census_filters(window))
        )
        return (await db.execute(stmt)).scalar_one()

    async def affected_rows(self, db: AsyncSession) -> list[EVTripMetrics]:
        window = await self.recorder_window()
        if window is None:
            return []
        stmt = (
            select(EVTripMetrics)
            .where(*self._census_filters(window))
            .order_by(EVTripMetrics.id)
        )
        return list((await db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # History fetch
    # ------------------------------------------------------------------

    async def _fetch_states(self) -> list[tuple[datetime, str, dict]]:
        """All trip-entity states in the window, ascending by state timestamp."""
        runtime = self._get_runtime()
        window = await self.recorder_window()
        if runtime is None or window is None:
            return []
        merged: list[tuple[datetime, str, dict]] = []
        for suffix in self.TRIP_ENTITY_SUFFIXES:
            entity_id = self._entity_id(runtime, suffix)
            if suffix == "events":
                states = await self._fetch_events_by_day(runtime, entity_id, window)
            else:
                states = await runtime._fetch_entity_history(
                    entity_id, window.isoformat()
                )
            for state in states:
                ts = _state_ts(state)
                if ts is None:
                    continue
                merged.append((ts, entity_id, state))
        merged.sort(key=lambda item: item[0])
        return merged

    @staticmethod
    async def _fetch_events_by_day(runtime, entity_id: str, window: datetime) -> list[dict]:
        """Fetch events history day-by-day to bound per-request payload size."""
        states: list[dict] = []
        cursor = window
        now = datetime.now(UTC)
        while cursor < now:
            day_end = min(cursor + timedelta(days=1), now)
            states.extend(
                await runtime._fetch_entity_history(
                    entity_id, cursor.isoformat(), end_time_iso=day_end.isoformat()
                )
            )
            cursor = day_end
        return states

    # ------------------------------------------------------------------
    # Replay core
    # ------------------------------------------------------------------

    async def _replay(
        self, db: AsyncSession, collect_diffs: bool
    ) -> tuple[dict[str, Any], list[RepairDiff]]:
        from web.services.sources.ha_fordpass import adapter
        from web.services.sources.ha_fordpass import handlers as fp_handlers
        from web.services.sources.ha_fordpass.dispatch import dispatch_slug

        details: dict[str, Any] = {
            "states_replayed": 0,
            "errors": 0,
            "trips_recovered": 0,
            "protected_reverted": 0,
            "rows_changed": 0,
            "filled": {},
        }
        runtime = self._get_runtime()
        window = await self.recorder_window()
        if runtime is None or window is None:
            return details, []

        ha_config = dict(runtime._ha_config or {})
        states = await self._fetch_states()

        window_stmt = select(EVTripMetrics).where(EVTripMetrics.end_time >= window)
        before_rows = (await db.execute(window_stmt)).scalars().all()
        # Before-images of ALL window trips (every source_system) — the
        # enrich paths don't check source_system, so the preservation
        # invariant is enforced here by reverting protected rows afterward.
        before = {row.id: serialize_row(row) for row in before_rows}

        for _ts, entity_id, state in states:
            try:
                if entity_id.endswith("_metrics"):
                    # Dispatching metrics would write an EVBatteryStatus row
                    # per state; the trip regen backfill is its only
                    # trip-relevant effect, so call it directly.
                    attrs = state.get("attributes") or {}
                    device_id = adapter._device_id_from_entity(entity_id) or "unknown"
                    recorded_at = adapter._parse_event_ts(state) or datetime.now(UTC)
                    await adapter._backfill_trip_regen(
                        entity_id, attrs, device_id, db, recorded_at, ha_config
                    )
                else:
                    await dispatch_slug(
                        entity_id, state, ha_config, db, config_id=SENTINEL_CONFIG_ID
                    )
                details["states_replayed"] += 1
            except Exception:
                logger.exception("recorder-replay: dispatch failed for %s", entity_id)
                details["errors"] += 1

        # Purge sentinel pending state WITHOUT flushing: replay must never
        # write EVVehicleStatus/EVBatteryStatus rows (trips-only scope).
        for cache in (
            fp_handlers._pending_vehicle_status,
            fp_handlers._pending_vehicle_status_ts,
            fp_handlers._pending_battery_status,
            fp_handlers._pending_battery_status_ts,
            fp_handlers._last_trip_values,
        ):
            for key in [k for k in cache if k[0] == SENTINEL_CONFIG_ID]:
                cache.pop(key, None)

        await db.flush()

        after_rows = (await db.execute(window_stmt)).scalars().all()

        # Revert changes replay made to non-mutable rows (D-05 enforcement).
        reverted_ids: set[int] = set()
        for row in after_rows:
            before_img = before.get(row.id)
            if before_img is None:
                continue
            if before_img.get("source_system") in MUTABLE_SOURCE_SYSTEMS:
                continue
            after_img = serialize_row(row)
            changed_fields = [
                f for f, v in after_img.items() if before_img.get(f) != v
            ]
            if not changed_fields:
                continue
            original = deserialize_row(EVTripMetrics, before_img)
            for f in changed_fields:
                setattr(row, f, original[f])
            details["protected_reverted"] += 1
            reverted_ids.add(row.id)
        if reverted_ids:
            await db.flush()

        # Per-field fill counts + diffs over mutable rows and new inserts.
        diffs: list[RepairDiff] = []
        filled: dict[str, int] = {}
        for row in after_rows:
            if row.id in reverted_ids:
                continue
            before_img = before.get(row.id)
            after_img = serialize_row(row)
            if before_img is None:
                details["trips_recovered"] += 1
                details["rows_changed"] += 1
                if collect_diffs:
                    diffs.append(RepairDiff(row.id, None, after_img, "insert"))
                continue
            if after_img.get("source_system") not in MUTABLE_SOURCE_SYSTEMS:
                continue
            changed = {
                f: after_img[f] for f in after_img if before_img.get(f) != after_img[f]
            }
            if not changed:
                continue
            details["rows_changed"] += 1
            for f in self.ENRICHABLE_FIELDS:
                if f in changed and before_img.get(f) is None and changed[f] is not None:
                    filled[f] = filled.get(f, 0) + 1
            if collect_diffs:
                diffs.append(
                    RepairDiff(
                        row.id,
                        {f: before_img.get(f) for f in changed},
                        changed,
                        "update",
                    )
                )
        details["filled"] = filled
        return details, diffs

    # ------------------------------------------------------------------
    # RepairOperation interface
    # ------------------------------------------------------------------

    async def preview(self, db: AsyncSession, limit: int = 10) -> list[RepairDiff]:
        """Dry-run replay in a rollback_session; `db` is unused (interface compat)."""
        async with rollback_session(engine=self._rollback_engine) as session:
            _details, diffs = await self._replay(session, collect_diffs=True)
        return diffs[:limit]

    async def execute(self, db: AsyncSession) -> int:
        details, _diffs = await self._replay(db, collect_diffs=False)
        self.last_details = details
        return details["rows_changed"]

    async def apply(self, db: AsyncSession) -> RepairResult:
        """Snapshot enrichable rows, then always replay — even at census 0.

        Unlike the base template, an empty census does not short-circuit:
        replay can still recover trips that were never ingested. Those
        recovered-trip inserts are not snapshotted (restore puts snapshotted
        rows back; re-running the op reconverges). run_id stays None when
        nothing was snapshotted.
        """
        from web.services.repair.snapshot import snapshot_rows

        rows = await self.affected_rows(db)
        run_id: uuid.UUID | None = None
        snapshot_count = 0
        if rows:
            for row in rows:
                if getattr(row, "source_system", None) not in MUTABLE_SOURCE_SYSTEMS:
                    raise ValueError(
                        f"repair '{self.slug}' targeted a non-mutable row "
                        f"(id={row.id}, source_system={row.source_system!r})"
                    )
            run_id = uuid.uuid4()
            snapshot_count = await snapshot_rows(db, run_id, self.slug, rows)
        affected = await self.execute(db)
        return RepairResult(
            self.slug, run_id, affected, snapshot_count, details=dict(self.last_details)
        )
