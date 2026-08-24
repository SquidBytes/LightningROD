"""Fill missing trip timing/odometer/efficiency fields from stored telemetry."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle_status import EVVehicleStatus
from web.queries.trips import detect_trip_location_ids
from web.services.repair.base import (
    RepairDiff,
    RepairOperation,
    RepairResult,
    _aware,
)

# Legacy pre-abstraction ingestion wrote source_system='homeassistant'; this
# op may enrich those rows, but the global MUTABLE_SOURCE_SYSTEMS stays
# narrow so other operations keep their smaller blast radius.
DERIVABLE_SOURCE_SYSTEMS = ("ha_fordpass", "homeassistant")

ODO_END_LOOKBACK = timedelta(minutes=30)
ODO_END_LOOKAHEAD = timedelta(minutes=5)
START_LOOKBACK = timedelta(hours=48)
IGNITION_LOOKBACK = timedelta(hours=12)
IGNITION_ODO_WINDOW = timedelta(minutes=5)
# Two-sided: an ON whose odometer sits far BELOW the trip's start value is an
# older journey's key-on, not this trip's start (one-sided checks accept it).
IGNITION_ODO_MATCH_KM = 1.0
ODO_START_TOLERANCE_KM = 0.5
DURATION_BAND_SECONDS = (60.0, 86400.0)
SPEED_BAND_KMH = (2.0, 180.0)


class TelemetryDerive(RepairOperation):
    """NULL-fill trip fields from the ev_vehicle_status timeline. Enrich only."""

    slug = "telemetry-derive"
    display_name = "Derive trip fields from telemetry"
    description = (
        "Fills missing trip timing, odometer, efficiency, and start/end "
        "location fields from vehicle telemetry and GPS history already "
        "stored in the database — no Home Assistant connection needed."
    )
    model = EVTripMetrics

    def __init__(self):
        # Detail counts from the most recent execute(), for route rendering.
        self.last_details: dict[str, Any] = {}
        self._ignition_fills = 0

    async def _candidates(self, db: AsyncSession) -> list[EVTripMetrics]:
        # Coarse SQL filter; whether anything is actually derivable is
        # decided per-row in _changes().
        stmt = (
            select(EVTripMetrics)
            .where(
                EVTripMetrics.source_system.in_(DERIVABLE_SOURCE_SYSTEMS),
                EVTripMetrics.end_time.is_not(None),
                or_(
                    EVTripMetrics.start_time.is_(None),
                    EVTripMetrics.duration.is_(None),
                    EVTripMetrics.odometer_start.is_(None),
                    EVTripMetrics.odometer_end.is_(None),
                    EVTripMetrics.start_location_id.is_(None),
                    EVTripMetrics.end_location_id.is_(None),
                    and_(
                        EVTripMetrics.efficiency.is_(None),
                        EVTripMetrics.distance > 0,
                        EVTripMetrics.energy_consumed > 0,
                    ),
                ),
            )
            .order_by(EVTripMetrics.id)
        )
        return list((await db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Telemetry lookups
    # ------------------------------------------------------------------

    async def _odometer_at_end(
        self, db: AsyncSession, device_id: str, end_time: datetime
    ) -> float | None:
        """Latest odometer reading in the window around trip end."""
        stmt = (
            select(EVVehicleStatus.odometer)
            .where(
                EVVehicleStatus.device_id == device_id,
                EVVehicleStatus.odometer.is_not(None),
                EVVehicleStatus.recorded_at >= end_time - ODO_END_LOOKBACK,
                EVVehicleStatus.recorded_at <= end_time + ODO_END_LOOKAHEAD,
            )
            .order_by(EVVehicleStatus.recorded_at.desc())
            .limit(1)
        )
        val = (await db.execute(stmt)).scalar_one_or_none()
        return float(val) if val is not None else None

    async def _start_from_odometer(
        self, db: AsyncSession, device_id: str, end_time: datetime, odo_start: float
    ) -> datetime | None:
        """Latest pre-end reading whose odometer sits at the trip's start value.

        Two-sided band: readings far BELOW the start value predate an earlier
        journey — their timestamps would inflate duration (and can slip past
        the speed gate on long trips).
        """
        stmt = (
            select(EVVehicleStatus.recorded_at)
            .where(
                EVVehicleStatus.device_id == device_id,
                EVVehicleStatus.odometer.is_not(None),
                EVVehicleStatus.odometer <= odo_start + ODO_START_TOLERANCE_KM,
                EVVehicleStatus.odometer >= odo_start - IGNITION_ODO_MATCH_KM,
                EVVehicleStatus.recorded_at >= end_time - START_LOOKBACK,
                EVVehicleStatus.recorded_at < end_time,
            )
            .order_by(EVVehicleStatus.recorded_at.desc())
            .limit(1)
        )
        return _aware((await db.execute(stmt)).scalar_one_or_none())

    async def _odometer_near(
        self, db: AsyncSession, device_id: str, ts: datetime
    ) -> float | None:
        """Odometer reading nearest to ts within the cross-check window."""
        stmt = select(EVVehicleStatus.recorded_at, EVVehicleStatus.odometer).where(
            EVVehicleStatus.device_id == device_id,
            EVVehicleStatus.odometer.is_not(None),
            EVVehicleStatus.recorded_at >= ts - IGNITION_ODO_WINDOW,
            EVVehicleStatus.recorded_at <= ts + IGNITION_ODO_WINDOW,
        )
        rows = (await db.execute(stmt)).all()
        if not rows:
            return None
        nearest = min(rows, key=lambda r: abs((_aware(r[0]) - ts).total_seconds()))
        return float(nearest[1])

    async def _start_from_ignition(
        self,
        db: AsyncSession,
        device_id: str,
        end_time: datetime,
        odo_start: float | None,
    ) -> datetime | None:
        """Latest OFF->ON ignition transition before trip end, odometer-checked."""
        stmt = (
            select(
                EVVehicleStatus.recorded_at,
                EVVehicleStatus.ignition_status,
                EVVehicleStatus.odometer,
            )
            .where(
                EVVehicleStatus.device_id == device_id,
                EVVehicleStatus.ignition_status.is_not(None),
                EVVehicleStatus.recorded_at >= end_time - IGNITION_LOOKBACK,
                EVVehicleStatus.recorded_at < end_time,
            )
            .order_by(EVVehicleStatus.recorded_at)
        )
        transitions: list[tuple[datetime, float | None]] = []
        prev = None
        for recorded_at, status, odometer in (await db.execute(stmt)).all():
            state = status.strip().upper()
            if state not in ("OFF", "ON"):
                continue  # 'Unsupported' etc. must not break OFF->ON pairing
            if state == "ON" and prev == "OFF":
                transitions.append((recorded_at, odometer))
            prev = state

        # Newest first. Destination key-cycles (odometer at trip END) sit
        # newest; scan back to the transition whose odometer matches the
        # trip's START value. Without an odometer to match, only the newest
        # transition is trustworthy — older ones can't be told apart from
        # other journeys.
        for recorded_at, odometer in reversed(transitions):
            start = _aware(recorded_at)
            odo_at_start = (
                float(odometer)
                if odometer is not None
                else await self._odometer_near(db, device_id, start)
            )
            if odo_start is None or odo_at_start is None:
                return start if (recorded_at, odometer) == transitions[-1] else None
            if abs(odo_at_start - odo_start) <= IGNITION_ODO_MATCH_KM:
                return start
        return None

    async def _mean_temps(
        self, db: AsyncSession, device_id: str, start_time: datetime, end_time: datetime
    ) -> tuple[float | None, float | None]:
        """Mean (outside, cabin) temperature over the trip window."""
        stmt = select(
            EVVehicleStatus.outside_temperature, EVVehicleStatus.cabin_temperature
        ).where(
            EVVehicleStatus.device_id == device_id,
            EVVehicleStatus.recorded_at >= start_time,
            EVVehicleStatus.recorded_at <= end_time,
        )
        rows = (await db.execute(stmt)).all()

        def mean(values: list) -> float | None:
            present = [float(v) for v in values if v is not None]
            return sum(present) / len(present) if present else None

        return mean([r[0] for r in rows]), mean([r[1] for r in rows])

    # ------------------------------------------------------------------
    # Derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _plausible(distance: float | None, seconds: float) -> bool:
        """Derived timing must imply a sane duration and average speed."""
        if not DURATION_BAND_SECONDS[0] <= seconds <= DURATION_BAND_SECONDS[1]:
            return False
        if distance is None or distance <= 0:
            return False
        speed_kmh = distance / (seconds / 3600.0)
        return SPEED_BAND_KMH[0] <= speed_kmh <= SPEED_BAND_KMH[1]

    async def _changes(self, db: AsyncSession, trip: EVTripMetrics) -> dict[str, Any]:
        """Fill values derivable for this trip; only NULL fields, never overwrites."""
        changes: dict[str, Any] = {}
        end_time = _aware(trip.end_time)
        if end_time is None:
            # _candidates() filters these out; without an end anchor every
            # derivation below is unanchored.
            return changes
        distance = float(trip.distance) if trip.distance is not None else None
        start_effective = _aware(trip.start_time)

        # Stored duration pins start_time exactly (ingestion computes start
        # the same way); no odometer heuristics or plausibility gate needed.
        if start_effective is None and trip.duration is not None:
            start_effective = end_time - timedelta(seconds=float(trip.duration))
            changes["start_time"] = start_effective

        odo_end = float(trip.odometer_end) if trip.odometer_end is not None else None
        if odo_end is None:
            odo_end = await self._odometer_at_end(db, trip.device_id, end_time)
            if odo_end is not None:
                changes["odometer_end"] = odo_end

        odo_start = None
        # No end odometer anchor -> skip odometer/timing derivation entirely.
        if odo_end is not None:
            odo_start = (
                float(trip.odometer_start) if trip.odometer_start is not None else None
            )
            if odo_start is None and distance is not None and distance > 0:
                odo_start = odo_end - distance
                changes["odometer_start"] = odo_start

            derived_start = None
            if start_effective is None and odo_start is not None:
                derived_start = await self._start_from_odometer(
                    db, trip.device_id, end_time, odo_start
                )

            candidate_start = start_effective or derived_start
            if candidate_start is not None and (
                derived_start is not None or trip.duration is None
            ):
                seconds = (end_time - candidate_start).total_seconds()
                if self._plausible(distance, seconds):
                    if derived_start is not None:
                        changes["start_time"] = derived_start
                        start_effective = derived_start
                    if trip.duration is None:
                        changes["duration"] = seconds

        # Sparse odometer history can leave start unknown; fall back to the
        # latest OFF->ON ignition transition before trip end.
        if start_effective is None:
            ign_start = await self._start_from_ignition(
                db, trip.device_id, end_time, odo_start
            )
            if ign_start is not None:
                seconds = (end_time - ign_start).total_seconds()
                if self._plausible(distance, seconds):
                    changes["start_time"] = ign_start
                    start_effective = ign_start
                    if trip.duration is None:
                        changes["duration"] = seconds
                    self._ignition_fills += 1

        if trip.efficiency is None and distance is not None and distance > 0:
            energy = (
                float(trip.energy_consumed)
                if trip.energy_consumed is not None
                else None
            )
            if energy is not None and energy > 0:
                # Same formula ingestion and manual entry store: km/kWh.
                changes["efficiency"] = distance / energy

        if start_effective is not None and (
            trip.outside_air_temp is None or trip.cabin_temp is None
        ):
            outside, cabin = await self._mean_temps(
                db, trip.device_id, start_effective, end_time
            )
            if trip.outside_air_temp is None and outside is not None:
                changes["outside_air_temp"] = outside
            if trip.cabin_temp is None and cabin is not None:
                changes["cabin_temp"] = cabin

        # Resolve endpoint FKs from GPS history. Uses the effective start
        # (possibly just derived above) so timing and location land together;
        # only known-location matches yield an id — unknown places stay NULL.
        need_start_loc = trip.start_location_id is None and start_effective is not None
        need_end_loc = trip.end_location_id is None
        if need_start_loc or need_end_loc:
            start_id, end_id = await detect_trip_location_ids(
                db, trip.device_id, start_effective or end_time, end_time
            )
            if need_start_loc and start_id is not None:
                changes["start_location_id"] = start_id
            if need_end_loc and end_id is not None:
                changes["end_location_id"] = end_id

        return changes

    async def _pending(
        self, db: AsyncSession, limit: int | None = None
    ) -> list[tuple[EVTripMetrics, dict[str, Any]]]:
        out: list[tuple[EVTripMetrics, dict[str, Any]]] = []
        for trip in await self._candidates(db):
            changes = await self._changes(db, trip)
            if changes:
                out.append((trip, changes))
                if limit is not None and len(out) >= limit:
                    break
        return out

    # ------------------------------------------------------------------
    # RepairOperation interface
    # ------------------------------------------------------------------

    async def census(self, db: AsyncSession) -> int:
        return len(await self._pending(db))

    async def preview(self, db: AsyncSession, limit: int = 10) -> list[RepairDiff]:
        diffs: list[RepairDiff] = []
        for trip, changes in await self._pending(db, limit=limit):
            before = {field: getattr(trip, field) for field in changes}
            diffs.append(RepairDiff(trip.id, before, changes, "update"))
        return diffs

    async def affected_rows(self, db: AsyncSession) -> list[EVTripMetrics]:
        return [trip for trip, _changes in await self._pending(db)]

    async def execute(self, db: AsyncSession) -> int:
        filled: dict[str, int] = {}
        self._ignition_fills = 0
        pending = await self._pending(db)
        for trip, changes in pending:
            for field, val in changes.items():
                setattr(trip, field, val)
                filled[field] = filled.get(field, 0) + 1
        if self._ignition_fills:
            filled["start_time_ignition"] = self._ignition_fills
        await db.flush()
        self.last_details = {"rows_changed": len(pending), "filled": filled}
        return len(pending)

    async def apply(self, db: AsyncSession) -> RepairResult:
        """Base template with the guard widened to DERIVABLE_SOURCE_SYSTEMS."""
        from web.services.repair.snapshot import snapshot_rows

        rows = await self.affected_rows(db)
        if not rows:
            return RepairResult(self.slug, None, 0, 0)
        for row in rows:
            if getattr(row, "source_system", None) not in DERIVABLE_SOURCE_SYSTEMS:
                raise ValueError(
                    f"repair '{self.slug}' targeted a non-mutable row "
                    f"(id={row.id}, source_system={row.source_system!r})"
                )
        run_id = uuid.uuid4()
        count = await snapshot_rows(db, run_id, self.slug, rows)
        affected = await self.execute(db)
        return RepairResult(
            self.slug, run_id, affected, count, details=dict(self.last_details)
        )
