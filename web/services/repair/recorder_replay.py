"""Recorder replay: re-drive HA recorder history through ingestion to fix trips."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import event, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.services.repair.base import (
    DEFAULT_PREVIEW_LIMIT,
    MUTABLE_SOURCE_SYSTEMS,
    RepairDiff,
    RepairGroup,
    RepairOperation,
    RepairPreview,
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
# Contributing states listed per row before the rest are summarized as a count.
_MAX_SOURCES = 6


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


class _StateAttribution:
    """Records which replayed state wrote each trip row, for preview evidence.

    Handlers flush (and sometimes commit) mid-replay, so the pending sets are
    gone by the time a dispatch returns; a flush listener is the only place
    they can still be read. `after_flush` is the hook rather than
    `before_flush` because a recovered trip has no primary key to key on until
    its INSERT has run.
    """

    def __init__(self, session: AsyncSession):
        self._session = session.sync_session
        self._current: tuple[str, datetime] | None = None
        self._touched: dict[int, list[tuple[str, datetime]]] = {}

    def __enter__(self) -> _StateAttribution:
        event.listen(self._session, "after_flush", self._on_flush)
        return self

    def __exit__(self, *exc_info) -> None:
        event.remove(self._session, "after_flush", self._on_flush)

    def dispatching(self, entity_id: str, ts: datetime) -> None:
        self._current = (entity_id, ts)

    def _on_flush(self, session, _flush_context) -> None:
        if self._current is None:
            return
        for obj in list(session.new) + list(session.dirty):
            if not isinstance(obj, EVTripMetrics) or obj.id is None:
                continue
            seen = self._touched.setdefault(obj.id, [])
            if self._current not in seen:
                seen.append(self._current)

    def sources_for(self, row: EVTripMetrics) -> list[tuple[str, datetime]]:
        return self._touched.get(row.id, [])


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
    runs_when_clean = True
    # Where replayed states came from; shown as preview evidence.
    source_label = "Home Assistant recorder history"

    TRIP_ENTITY_SUFFIXES = ("events", "metrics", "elveh")
    # What makes a replayed trip recognisable when only a few fields changed.
    IDENTITY_FIELDS = ("trip_id", "start_time", "end_time", "distance")
    # ha-fordpass localizes these entities' trip attributes into Home
    # Assistant's unit system before emitting them, so their numbers cannot be
    # read without knowing it. The events payload is always raw metric.
    UNIT_SENSITIVE_SUFFIXES = ("metrics", "elveh")
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

    async def _fetch_states(self) -> list[tuple[datetime, str, dict, dict | None]]:
        """All trip-entity states in the window, ascending by state timestamp.

        The fourth element is the config a state must be read under, or None
        to fall back to the runtime's. Recorder history all comes from one
        live runtime, so it is always None here; sources whose states carry
        their own capture-time config return it per state.
        """
        runtime = self._get_runtime()
        window = await self.recorder_window()
        if runtime is None or window is None:
            return []
        merged: list[tuple[datetime, str, dict, dict | None]] = []
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
                merged.append((ts, entity_id, state, None))
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
    ) -> tuple[dict[str, Any], list[RepairGroup]]:
        from web.services.sources.ha_fordpass import adapter
        from web.services.sources.ha_fordpass import handlers as fp_handlers
        from web.services.sources.ha_fordpass.dispatch import dispatch_slug

        details: dict[str, Any] = {
            "states_replayed": 0,
            "errors": 0,
            "trips_recovered": 0,
            "protected_reverted": 0,
            "rows_changed": 0,
            "skipped_unknown_units": 0,
            "filled": {},
        }
        runtime = self._get_runtime()
        window = await self.recorder_window()
        if runtime is None or window is None:
            return details, []

        runtime_config = dict(runtime._ha_config or {})
        unit_sensitive = tuple(f"_{s}" for s in self.UNIT_SENSITIVE_SUFFIXES)
        states = await self._fetch_states()

        window_stmt = select(EVTripMetrics).where(EVTripMetrics.end_time >= window)
        before_rows = (await db.execute(window_stmt)).scalars().all()
        # Before-images of ALL window trips (every source_system) — the
        # enrich paths don't check source_system, so the preservation
        # invariant is enforced here by reverting protected rows afterward.
        before = {row.id: serialize_row(row) for row in before_rows}

        attribution = _StateAttribution(db)
        with attribution:
            for ts, entity_id, state, state_config in states:
                attribution.dispatching(entity_id, ts)
                try:
                    # A state that recorded its own config is read under it; the
                    # runtime's is only the fallback for states that carry none.
                    ha_config = state_config or runtime_config
                    if not ha_config.get("unit_system") and entity_id.endswith(
                        unit_sensitive
                    ):
                        # Guessing metric here silently stores miles as kilometres
                        # and °F as °C, and the wrong distance spawns a duplicate
                        # trip alongside the correct one. Skipping loses nothing
                        # the events payload does not already carry.
                        if not details["skipped_unknown_units"]:
                            logger.warning(
                                "%s: no unit system for %s — skipping states whose "
                                "values depend on it",
                                self.slug,
                                entity_id,
                            )
                        details["skipped_unknown_units"] += 1
                        continue
                    if entity_id.endswith("_metrics"):
                        # Dispatching metrics would write an EVBatteryStatus row
                        # per state; the trip regen backfill is its only
                        # trip-relevant effect, so call it directly.
                        attrs = state.get("attributes") or {}
                        device_id = (
                            adapter._device_id_from_entity(entity_id) or "unknown"
                        )
                        recorded_at = adapter._parse_event_ts(state) or datetime.now(
                            UTC
                        )
                        await adapter._backfill_trip_regen(
                            entity_id, attrs, device_id, db, recorded_at, ha_config
                        )
                    else:
                        await dispatch_slug(
                            entity_id,
                            state,
                            ha_config,
                            db,
                            config_id=SENTINEL_CONFIG_ID,
                        )
                    details["states_replayed"] += 1
                except Exception:
                    logger.exception(
                        "recorder-replay: dispatch failed for %s", entity_id
                    )
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
        groups: list[RepairGroup] = []
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
                    groups.append(
                        self._group(
                            RepairDiff(row.id, None, after_img, "insert"),
                            after_img,
                            window,
                            attribution.sources_for(row),
                        )
                    )
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
                groups.append(
                    self._group(
                        RepairDiff(
                            row.id,
                            {f: before_img.get(f) for f in changed},
                            changed,
                            "update",
                            identity={
                                f: after_img.get(f) for f in self.IDENTITY_FIELDS
                            },
                        ),
                        after_img,
                        window,
                        attribution.sources_for(row),
                    )
                )
        details["filled"] = filled
        return details, groups

    def _group(
        self,
        diff: RepairDiff,
        after_img: dict[str, Any],
        window: datetime,
        sources: list[tuple[str, datetime]],
    ) -> RepairGroup:
        """Wrap one replayed row with the states and window it came from."""
        context: dict[str, Any] = {}
        shown = [f"{entity_id} at {ts}" for entity_id, ts in sources[:_MAX_SOURCES]]
        if shown:
            extra = len(sources) - len(shown)
            context["states applied"] = "; ".join(shown) + (
                f"; +{extra} more" if extra > 0 else ""
            )
        if diff.action == "insert":
            context["recovered"] = (
                f"no trip row existed for trip_id {after_img.get('trip_id')} "
                "before the replay"
            )
        context["replayed from"] = self.source_label
        context["history window"] = f"since {window}"
        return RepairGroup([diff], label=f"Trip #{diff.row_id}", context=context)

    # ------------------------------------------------------------------
    # RepairOperation interface
    # ------------------------------------------------------------------

    async def preview(
        self, db: AsyncSession, limit: int = DEFAULT_PREVIEW_LIMIT, offset: int = 0
    ) -> RepairPreview:
        """Dry-run replay in a rollback_session; `db` is unused (interface compat)."""
        async with rollback_session(engine=self._rollback_engine) as session:
            _details, groups = await self._replay(session, collect_diffs=True)
        return RepairPreview(
            groups[offset : offset + limit], len(groups), offset, limit
        )

    async def execute(self, db: AsyncSession) -> int:
        details, _groups = await self._replay(db, collect_diffs=False)
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
