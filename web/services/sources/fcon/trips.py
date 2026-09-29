"""Trips derived from FordConnect telemetry.

A trip is an ignition-ON span in which the odometer moves. A park with the
vehicle left running stays one trip; switching off ends it. Remote start
sets ignition ON with the vehicle stationary, so a span without odometer
movement is not a trip. `tripProgress` is not used yet.

Like charge sessions, trips are folded from the raw archive when the
ignition-off sample arrives, so nothing is held in memory.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.queries.trips import detect_trip_location_ids
from web.services.sources.fcon.history import telemetry_since_boundary
from web.services.sources.fcon.identity import SOURCE_SYSTEM
from web.services.sources.ford_metrics.parser import parse_time
from web.services.trip_merge import apply_merge, find_same_drive, is_mergeable

logger = logging.getLogger("lightningrod.sources.fcon.trips")

INGEST_SCHEMA_VERSION = 2
IGNITION_ON = frozenset({"ON", "RUN", "START"})


def _entry(metrics: dict, name: str) -> dict:
    entry = metrics.get(name)
    return entry if isinstance(entry, dict) else {}


def _float(metrics: dict, name: str) -> float | None:
    value = _entry(metrics, name).get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass(frozen=True)
class TripSample:
    # When the ignition last reported, falling back to the payload's time.
    at: datetime
    ignition_on: bool
    odometer: float | None
    energy: float | None
    outside_temp: float | None
    brake_torque: float | None
    brake_torque_at: datetime | None
    range_regenerated: float | None

    @classmethod
    def from_payload(cls, payload: dict) -> TripSample | None:
        m = payload.get("metrics") or {}
        ignition = _entry(m, "ignitionStatus")
        at = parse_time(ignition.get("updateTime")) or parse_time(payload.get("updateTime"))
        if at is None:
            return None
        return cls(
            at=at,
            ignition_on=str(ignition.get("value") or "").upper() in IGNITION_ON,
            odometer=_float(m, "odometer"),
            energy=_float(m, "xevBatteryEnergyRemaining"),
            outside_temp=_float(m, "outsideTemperature"),
            brake_torque=_float(m, "brakeTorque"),
            brake_torque_at=parse_time(_entry(m, "brakeTorque").get("updateTime")),
            range_regenerated=_float(m, "tripXevBatteryRangeRegenerated"),
        )


def _last(values: list[float | None]) -> float | None:
    return next((v for v in reversed(values) if v is not None), None)


def derive_trip(samples: list[TripSample]) -> dict[str, Any] | None:
    """Trip columns for samples ending at ignition-off; None when it never moved."""
    driving = [s for s in samples if s.ignition_on]
    if not driving or samples[-1].ignition_on:
        return None
    start, end = driving[0], samples[-1]
    odometers = [s.odometer for s in samples if s.odometer is not None]
    odo_start = next((s.odometer for s in samples if s.odometer is not None), None)
    if odo_start is None:
        return None
    # Clamped: an odometer reading never moves backwards.
    odo_end = max(odometers)
    distance = round(odo_end - odo_start, 3)
    if distance <= 0:
        return None
    energies = [s.energy for s in samples]
    first_energy = next((e for e in energies if e is not None), None)
    last_energy = _last(energies)
    energy = (
        round(max(0.0, first_energy - last_energy), 3)
        if first_energy is not None and last_energy is not None
        else None
    )
    # Peak of the instantaneous Nm readings the vehicle refreshed during this
    # drive; at one snapshot a minute it is a sampled lower bound, not braking
    # behaviour (that needs the per-sample ev_vehicle_status series).
    torques = [
        s.brake_torque
        for s in samples
        if s.brake_torque is not None
        and s.brake_torque_at is not None
        and start.at <= s.brake_torque_at <= end.at
    ]
    return {
        "start_time": start.at,
        "end_time": end.at,
        "duration": (end.at - start.at).total_seconds(),
        "distance": distance,
        "odometer_start": odo_start,
        "odometer_end": odo_end,
        "energy_consumed": energy,
        "efficiency": round(distance / energy, 3) if energy else None,
        "range_regenerated": _last([s.range_regenerated for s in samples]),
        "ambient_temp": _last([s.outside_temp for s in samples]),
        "brake_torque": max(torques) if torques else None,
    }


def trip_uuid(device_id: str, start: datetime) -> uuid.UUID:
    """Stable id: the same drive re-derived keeps its row."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"lightningrod:fcon-trip:{device_id}|{start.isoformat()}")


def _is_ignition_off(payload: dict) -> bool:
    sample = TripSample.from_payload(payload)
    return sample is not None and not sample.ignition_on


async def record_trip_if_ended(
    db: AsyncSession, device_id: str, payload: dict
) -> EVTripMetrics | None:
    """If this telemetry sample switches the vehicle off after a drive, write the trip."""
    current = TripSample.from_payload(payload)
    if current is None or current.ignition_on:
        return None
    until = parse_time(payload.get("updateTime")) or current.at
    payloads = await telemetry_since_boundary(db, device_id, until, _is_ignition_off)
    samples = [s for p in payloads if (s := TripSample.from_payload(p)) is not None]
    if not samples or samples[-1] != current:
        samples.append(current)
    fields = derive_trip(samples)
    if fields is None:
        return None
    start_loc, end_loc = await detect_trip_location_ids(
        db, device_id, fields["start_time"], fields["end_time"]
    )
    fields.update(start_location_id=start_loc, end_location_id=end_loc)

    trip_id = trip_uuid(device_id, fields["start_time"])
    existing = await db.scalar(select(EVTripMetrics).where(EVTripMetrics.trip_id == trip_id))
    if existing is not None and existing.source_system == SOURCE_SYSTEM:
        # Re-deriving an unmerged drive (a replay or re-parse) refreshes it.
        for column, value in fields.items():
            setattr(existing, column, value)
        return existing
    existing = existing or await find_same_drive(db, {"device_id": device_id, **fields})
    if existing is not None:
        if is_mergeable(existing):
            apply_merge(existing, {**fields, "source_system": SOURCE_SYSTEM}, SOURCE_SYSTEM)
            return existing
        logger.info("fcon trip matches protected trip %s; not written", existing.id)
        return None
    trip = EVTripMetrics(
        trip_id=trip_id,
        device_id=device_id,
        source_system=SOURCE_SYSTEM,
        is_complete=True,
        recorded_at=fields["end_time"],
        original_timestamp=fields["start_time"],
        ingest_schema_version=INGEST_SCHEMA_VERSION,
        **fields,
    )
    db.add(trip)
    await db.flush()
    logger.info("Derived fcon trip: %s km, %s kWh", fields["distance"], fields["energy_consumed"])
    return trip
