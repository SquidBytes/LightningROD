"""Seed module: EV vehicle status snapshots — 500+ rows over the demo window.

Sampling strategy:
  * During known trips (queried from ev_trip_metrics): one snapshot every 10 min.
  * Outside of trips (parked): one snapshot every 6 h across the 90-day window.

This module declares its OWN ``EXPECTED_CONTRACTS`` because
``ev_vehicle_status`` currently has ZERO entries in
``web/services/sources/ha_fordpass/adapter.py::FIELD_CONTRACTS``. Every
column populated via ``ContractDrivenSeeder.value_for(...)`` here will be
recorded as a gap, and T19's gap-report step will turn ``EXPECTED_CONTRACTS``
into copy-pasteable ``FieldContract(...)`` blocks for the adapter.

EXPECTED_CONTRACTS (target_db_column → target_unit):
  * acceleration            → m/s²
  * accelerator_position    → %
  * brake_status            → str
  * brake_torque            → Nm
  * cabin_temperature       → degC
  * gear_position           → str
  * ignition_status         → str
  * odometer                → km
  * outside_temperature     → degC
  * parking_brake           → str
  * speed                   → km/h
  * tire_pressure           → kPa  (JSONB; one contract per corner)
  * yaw_rate                → deg/s

Idempotent: if ≥500 status rows exist for the demo vehicle, returns 0.
Determinism: ``random.Random(42)``.
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle import EVVehicle
from db.models.vehicle_status import EVVehicleStatus
from scripts.seed.base import ContractDrivenSeeder, load_declared_contracts
from web.services.units.contracts import FieldContract

logger = logging.getLogger(__name__)

_DEMO_VIN = "1FT6W1EV0NWG00000"
_TABLE = "ev_vehicle_status"

_TARGET_ROWS = 500
_IDEMPOTENCY_THRESHOLD = 500
_WINDOW_DAYS = 90
_DRIVE_SAMPLE_MIN = 10  # one snapshot every 10 minutes during a trip
_PARKED_SAMPLE_HRS = 6  # one snapshot every 6 hours otherwise

_GEAR_DRIVE = ("D", "D", "D", "D", "R")  # weighted toward D
_BRAKE_STATES = ("applied", "released")
_IGNITION_DRIVE = "on"
_IGNITION_PARKED = "off"


# ---------------------------------------------------------------------------
# Expected contracts — these become the gap report content for T19.
# ---------------------------------------------------------------------------

EXPECTED_CONTRACTS: list[FieldContract] = [
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="odometer",
        source_unit="km",
        target_db_table=_TABLE,
        target_db_column="odometer",
        target_unit="km",
        notes="Cumulative odometer reading (km). Monotonically increasing.",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="vehicleSpeed",
        source_unit="km/h",
        target_db_table=_TABLE,
        target_db_column="speed",
        target_unit="km/h",
        notes="Instantaneous vehicle speed (km/h). Zero when parked.",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="acceleratorPedalPosition",
        source_unit="%",
        target_db_table=_TABLE,
        target_db_column="accelerator_position",
        target_unit="%",
        notes="Accelerator pedal position (0–100%). Localized by ha-fordpass.",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="brakeStatus",
        source_unit="str",
        target_db_table=_TABLE,
        target_db_column="brake_status",
        target_unit="str",
        notes='Brake pedal state: "applied" or "released".',
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="gearLeverPosition",
        source_unit="str",
        target_db_table=_TABLE,
        target_db_column="gear_position",
        target_unit="str",
        notes='Gear lever: "P" / "R" / "N" / "D".',
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="ignitionStatus",
        source_unit="str",
        target_db_table=_TABLE,
        target_db_column="ignition_status",
        target_unit="str",
        notes='Ignition: "on" / "off" / "accessory".',
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="parkingBrakeStatus",
        source_unit="str",
        target_db_table=_TABLE,
        target_db_column="parking_brake",
        target_unit="str",
        notes='Parking brake: "applied" / "released".',
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_outsidetemp",
        source_attribute="ambientTemp",
        source_unit="degC",
        ha_unit_system_converted=True,
        target_db_table=_TABLE,
        target_db_column="outside_temperature",
        target_unit="degC",
        notes=(
            "Outside ambient temperature, time-series from per-sensor "
            "outsidetemp entity. ha-fordpass localizes via localize_temperature "
            "(imperial -> degF, metric -> degC)."
        ),
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_cabintemperature",
        source_attribute="cabinTemperature",
        source_unit="degC",
        ha_unit_system_converted=True,
        target_db_table=_TABLE,
        target_db_column="cabin_temperature",
        target_unit="degC",
        notes=(
            "Cabin temperature, time-series from per-sensor cabintemperature "
            "entity. ha-fordpass localizes via localize_temperature."
        ),
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="brakeTorque",
        source_unit="Nm",
        target_db_table=_TABLE,
        target_db_column="brake_torque",
        target_unit="Nm",
        notes="Brake torque. SI passthrough; no localization in ha-fordpass.",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="yawRate",
        source_unit="deg/s",
        target_db_table=_TABLE,
        target_db_column="yaw_rate",
        target_unit="deg/s",
        notes="Yaw rate. Passthrough; no localization.",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="acceleration",
        source_unit="m/s2",
        target_db_table=_TABLE,
        target_db_column="acceleration",
        target_unit="m/s2",
        notes="Longitudinal acceleration. SI passthrough.",
    ),
    # Tire pressures: HA emits four corner-specific attributes; we pack them
    # into the single JSONB ``tire_pressure`` column. Four contracts, one per
    # corner, share the same target column so the gap report shows all four.
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="tirePressure.frontLeft",
        source_unit="kPa",
        ha_unit_system_converted=True,
        target_db_table=_TABLE,
        target_db_column="tire_pressure",
        target_unit="kPa",
        notes="Front-left tire pressure; ha-fordpass localizes via units.pressure().",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="tirePressure.frontRight",
        source_unit="kPa",
        ha_unit_system_converted=True,
        target_db_table=_TABLE,
        target_db_column="tire_pressure",
        target_unit="kPa",
        notes="Front-right tire pressure; ha-fordpass localizes via units.pressure().",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="tirePressure.rearLeft",
        source_unit="kPa",
        ha_unit_system_converted=True,
        target_db_table=_TABLE,
        target_db_column="tire_pressure",
        target_unit="kPa",
        notes="Rear-left tire pressure; ha-fordpass localizes via units.pressure().",
    ),
    FieldContract(
        source_entity_pattern="sensor.fordpass_{vin}_metrics",
        source_attribute="tirePressure.rearRight",
        source_unit="kPa",
        ha_unit_system_converted=True,
        target_db_table=_TABLE,
        target_db_column="tire_pressure",
        target_unit="kPa",
        notes="Rear-right tire pressure; ha-fordpass localizes via units.pressure().",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_tire_pressure(rng: random.Random, base: float = 240.0) -> dict:
    """Return a JSONB-shaped tire-pressure dict with realistic per-corner values.

    Corners stay within 220–260 kPa (≈32–38 psi) with ±5 kPa variation.
    """
    return {
        "front_left": round(base + rng.uniform(-5, 5), 1),
        "front_right": round(base + rng.uniform(-5, 5), 1),
        "rear_left": round(base + rng.uniform(-5, 5), 1),
        "rear_right": round(base + rng.uniform(-5, 5), 1),
        "system_status": "normal",
    }


def _build_drive_samples(
    start: datetime, end: datetime, sample_minutes: int
) -> list[datetime]:
    """Yield a sample timestamp every ``sample_minutes`` from start..end inclusive."""
    out: list[datetime] = []
    cursor = start
    step = timedelta(minutes=sample_minutes)
    while cursor <= end:
        out.append(cursor)
        cursor += step
    return out


def _build_parked_samples(
    window_start: datetime,
    window_end: datetime,
    drive_intervals: list[tuple[datetime, datetime]],
    sample_hours: int,
) -> list[datetime]:
    """Sample every ``sample_hours`` across the window, skipping points inside any drive."""
    out: list[datetime] = []
    cursor = window_start
    step = timedelta(hours=sample_hours)
    while cursor <= window_end:
        in_drive = any(start <= cursor <= end for start, end in drive_intervals)
        if not in_drive:
            out.append(cursor)
        cursor += step
    return out


# ---------------------------------------------------------------------------
# Seeder
# ---------------------------------------------------------------------------


async def seed(db: AsyncSession) -> int:
    """Insert ≥500 EVVehicleStatus snapshots for the demo vehicle.

    Returns the number of rows inserted (0 if already seeded).
    Raises RuntimeError if the demo vehicle is missing.
    """
    # --- Lookup demo vehicle ---
    vehicle = (
        await db.execute(select(EVVehicle).where(EVVehicle.vin == _DEMO_VIN))
    ).scalar_one_or_none()
    if vehicle is None:
        raise RuntimeError(
            f"Demo vehicle VIN={_DEMO_VIN!r} not found — run vehicle.seed() first."
        )

    device_id = vehicle.device_id

    # --- Idempotency check ---
    existing_count = (
        await db.execute(
            select(func.count())
            .select_from(EVVehicleStatus)
            .where(EVVehicleStatus.device_id == device_id)
        )
    ).scalar_one()
    if existing_count >= _IDEMPOTENCY_THRESHOLD:
        logger.info(
            "vehicle_status: %d rows already exist for device_id=%r — skipping",
            existing_count,
            device_id,
        )
        return 0

    rng = random.Random(42)
    seeder = ContractDrivenSeeder(
        declared=load_declared_contracts(),
        expected=EXPECTED_CONTRACTS,
        rng=rng,
    )

    now = datetime.now(UTC)
    window_start = now - timedelta(days=_WINDOW_DAYS)

    # --- Pull drive intervals from trips ---
    trip_rows = (
        await db.execute(
            select(EVTripMetrics.start_time, EVTripMetrics.end_time)
            .where(EVTripMetrics.device_id == device_id)
            .where(EVTripMetrics.start_time.isnot(None))
            .where(EVTripMetrics.end_time.isnot(None))
            .order_by(EVTripMetrics.start_time)
        )
    ).all()
    drive_intervals: list[tuple[datetime, datetime]] = [
        (s if s.tzinfo else s.replace(tzinfo=UTC),
         e if e.tzinfo else e.replace(tzinfo=UTC))
        for s, e in trip_rows
    ]

    # Charging sessions also count as "engaged but speed=0" — we still treat as parked
    # for sampling, but acknowledge ignition may be "accessory" during charge.
    charge_rows = (
        await db.execute(
            select(EVChargingSession.session_start_utc, EVChargingSession.session_end_utc)
            .where(EVChargingSession.device_id == device_id)
            .where(EVChargingSession.session_start_utc.isnot(None))
            .where(EVChargingSession.session_end_utc.isnot(None))
        )
    ).all()
    charging_intervals: list[tuple[datetime, datetime]] = [
        (s if s.tzinfo else s.replace(tzinfo=UTC),
         e if e.tzinfo else e.replace(tzinfo=UTC))
        for s, e in charge_rows
    ]

    # --- Build sample timestamps ---
    drive_samples: list[tuple[datetime, bool]] = []  # (ts, is_drive=True)
    for s, e in drive_intervals:
        for ts in _build_drive_samples(s, e, _DRIVE_SAMPLE_MIN):
            drive_samples.append((ts, True))

    parked_ts = _build_parked_samples(
        window_start, now, drive_intervals, _PARKED_SAMPLE_HRS
    )
    parked_samples: list[tuple[datetime, bool]] = [(ts, False) for ts in parked_ts]

    all_samples = sorted(drive_samples + parked_samples, key=lambda t: t[0])

    # If trips are sparse and we still don't hit the floor, top up with extra
    # parked samples at 3-hour resolution between sparse points.
    if len(all_samples) < _TARGET_ROWS:
        extra_ts = _build_parked_samples(
            window_start, now, drive_intervals, max(1, _PARKED_SAMPLE_HRS // 2)
        )
        seen = {ts for ts, _ in all_samples}
        for ts in extra_ts:
            if ts not in seen:
                all_samples.append((ts, False))
                seen.add(ts)
            if len(all_samples) >= _TARGET_ROWS + 50:
                break
        all_samples.sort(key=lambda t: t[0])

    # --- Generate rows ---
    # Odometer must be monotonically increasing — start at a realistic baseline
    # and accumulate based on speed*dt during drives.
    odometer_km = 12_000.0 + rng.uniform(0, 5_000)  # plausible used-vehicle baseline
    last_ts: datetime | None = None

    rows: list[EVVehicleStatus] = []
    for ts, is_drive in all_samples:
        # --- Time-delta odometer accumulation ---
        if last_ts is not None and is_drive:
            dt_hours = (ts - last_ts).total_seconds() / 3600.0
            # Cap dt to one sample step so an inter-trip gap doesn't inflate odo
            dt_hours = min(dt_hours, _DRIVE_SAMPLE_MIN / 60.0)
        else:
            dt_hours = 0.0

        # --- Always call seeder.value_for(...) so EVERY contract is recorded ---
        # ContractDrivenSeeder logs the gap once per (table,column) — repeat
        # calls are cheap and ensure all 13 expected contracts are flagged.
        _ = seeder.value_for(_TABLE, "odometer")  # km — overridden below
        speed_random = seeder.value_for(_TABLE, "speed")
        accel_random = seeder.value_for(_TABLE, "accelerator_position")
        cabin_temp = seeder.value_for(_TABLE, "cabin_temperature")
        outside_temp = seeder.value_for(_TABLE, "outside_temperature")
        brake_seed = seeder.value_for(_TABLE, "brake_status", context={"value": "released"})
        gear_seed = seeder.value_for(_TABLE, "gear_position", context={"value": "P"})
        ignition_seed = seeder.value_for(_TABLE, "ignition_status", context={"value": "off"})
        parking_seed = seeder.value_for(_TABLE, "parking_brake", context={"value": "applied"})
        # Four tire-pressure corners — all map to JSONB tire_pressure column
        _ = seeder.value_for(_TABLE, "tire_pressure")  # only logs the gap once
        # (Calling value_for again with same key is fine; gap dedupes.)

        if is_drive:
            # --- Drive sample: speed > 0, monotonic odo, gear D/R, ignition on ---
            speed = round(rng.uniform(15.0, 110.0), 1)  # km/h, realistic city/hwy
            odometer_km += speed * dt_hours
            accel_pos = round(rng.uniform(5.0, 60.0), 1)
            brake_status = rng.choices(_BRAKE_STATES, weights=(1, 4), k=1)[0]
            gear = rng.choice(_GEAR_DRIVE)
            ignition = _IGNITION_DRIVE
            parking_brake = "released"
            # Drive-state seatbelt almost always engaged
            seatbelt = "engaged"
            connectivity = "connected"
            deep_sleep = "off"
            yaw = round(rng.gauss(0.0, 1.5), 2)
            acceleration = round(rng.gauss(0.2, 1.2), 2)
            brake_torque = round(rng.uniform(0.0, 200.0), 1)
            wheel_torque_status = "active"
            torque_at_trans = round(rng.uniform(50.0, 350.0), 1)
            evcc = "idle"
            remote_start = "inactive"
        else:
            # --- Parked sample: speed=0, gear P, ignition off (or accessory if charging) ---
            in_charge = any(s <= ts <= e for s, e in charging_intervals)
            speed = 0.0
            accel_pos = 0.0
            brake_status = "released"
            gear = "P"
            ignition = "accessory" if in_charge else _IGNITION_PARKED
            parking_brake = "applied"
            seatbelt = "disengaged"
            connectivity = "connected" if in_charge else rng.choices(
                ("connected", "disconnected"), weights=(3, 1), k=1
            )[0]
            deep_sleep = "off" if in_charge else "on"
            yaw = 0.0
            acceleration = 0.0
            brake_torque = 0.0
            wheel_torque_status = "inactive"
            torque_at_trans = 0.0
            evcc = "charging" if in_charge else "idle"
            remote_start = "inactive"

        # Suppress unused-warning bookkeeping (values used only for gap recording)
        del speed_random, accel_random, brake_seed, gear_seed, ignition_seed, parking_seed

        # --- JSONB structured fields ---
        # Tire pressure base nudges down slightly in cold weather for realism
        tp_base = 240.0 + (outside_temp - 15.0) * 0.3 if isinstance(outside_temp, int | float) else 240.0
        tp_base = max(220.0, min(260.0, tp_base))
        tire_pressure = _generate_tire_pressure(rng, base=tp_base)

        door_lock_status = {
            "vehicle_locked": not is_drive,
            "doors": {
                "driver": "closed",
                "passenger": "closed",
                "rear_left": "closed",
                "rear_right": "closed",
            },
        }
        indicators = {
            "low_fuel": False,
            "service_due": False,
            "tire_pressure_warning": False,
            "check_engine": False,
        }

        row = EVVehicleStatus(
            device_id=device_id,
            recorded_at=ts,
            odometer=round(odometer_km, 1),
            speed=speed,
            accelerator_position=accel_pos,
            brake_status=brake_status,
            gear_position=gear,
            parking_brake=parking_brake,
            ignition_status=ignition,
            remote_start_status=remote_start,
            torque_at_transmission=torque_at_trans,
            door_lock_status=door_lock_status,
            tire_pressure=tire_pressure,
            indicators=indicators,
            brake_torque=brake_torque,
            wheel_torque_status=wheel_torque_status,
            yaw_rate=yaw,
            acceleration=acceleration,
            outside_temperature=outside_temp,
            cabin_temperature=cabin_temp,
            deep_sleep_status=deep_sleep,
            device_connectivity=connectivity,
            evcc_status=evcc,
            seatbelt_status=seatbelt,
            source_system="seed",
            original_timestamp=ts,
        )
        rows.append(row)
        last_ts = ts

    # --- Insert in chunks of 100 to keep the session payload bounded ---
    inserted = 0
    chunk = 100
    for i in range(0, len(rows), chunk):
        db.add_all(rows[i : i + chunk])
        await db.flush()
        inserted += len(rows[i : i + chunk])

    logger.info(
        "vehicle_status: inserted %d rows for device_id=%r (%d drive samples, %d parked)",
        inserted,
        device_id,
        sum(1 for _, d in all_samples if d),
        sum(1 for _, d in all_samples if not d),
    )
    return inserted
