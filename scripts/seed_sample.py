"""Seed sample vehicle, battery status, charging sessions, and trip metrics into PostgreSQL.

Creates a sample 2024 F-150 Lightning SR XLT, sets it as the active vehicle,
then seeds correlated battery telemetry, charging session, and trip metric data
so that all records align for testing.

Usage:
    uv run python scripts/seed_sample.py
    uv run python scripts/seed_sample.py --dry-run
    uv run python scripts/seed_sample.py --battery-only
    uv run python scripts/seed_sample.py --sessions-only
    uv run python scripts/seed_sample.py --trips-only
    uv run python scripts/seed_sample.py --device-id CUSTOM_ID
"""

import argparse
import asyncio
import csv
import hashlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.engine import AsyncSessionLocal
from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle import EVVehicle

SOURCE_SYSTEM = "sample_generator"

# Sample vehicle definition
SAMPLE_VEHICLE = {
    "display_name": "F-150 Lightning SR",
    "make": "Ford",
    "model": "F-150 Lightning",
    "year": 2024,
    "trim": "XLT",
    "battery_capacity_kwh": 98.0,
    "vin": "1FT8W3ED5LFB0D19",
    "source_system": SOURCE_SYSTEM,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def float_or_none(v: str) -> Optional[float]:
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def str_or_none(v: str) -> Optional[str]:
    v = v.strip() if v else ""
    return v if v else None


def int_or_none(v: str) -> Optional[int]:
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def parse_timestamp(v: str) -> Optional[datetime]:
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def parse_uuid(v: str) -> Optional[uuid.UUID]:
    v = v.strip() if v else ""
    if not v:
        return None
    try:
        return uuid.UUID(v)
    except (ValueError, AttributeError):
        return None


def parse_bool(v: str) -> bool:
    return v.strip().lower() in ("true", "1", "yes") if v else False


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------
# The sample-data CSVs are generated in US units (miles, °F, mi/kWh) for
# readability, but the schema stores metric base (km, °C, km/kWh) to match
# the hass_processor pipeline. Convert on load so seed and HASS data are
# interchangeable.

_MI_TO_KM = 1.60934


def _mi_to_km(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v * _MI_TO_KM, 4)


def _f_to_c(v: Optional[float]) -> Optional[float]:
    return None if v is None else round((v - 32.0) * 5.0 / 9.0, 3)


# ---------------------------------------------------------------------------
# Battery status transform
# ---------------------------------------------------------------------------

def transform_battery_row(csv_row: dict, device_id: str) -> Optional[dict]:
    db_row = {
        "recorded_at": parse_timestamp(csv_row.get("recorded_at", "")),
        "hv_battery_soc": float_or_none(csv_row.get("hv_battery_soc", "")),
        "hv_battery_actual_soc": float_or_none(csv_row.get("hv_battery_actual_soc", "")),
        "hv_battery_voltage": float_or_none(csv_row.get("hv_battery_voltage", "")),
        "hv_battery_amperage": float_or_none(csv_row.get("hv_battery_amperage", "")),
        "hv_battery_kw": float_or_none(csv_row.get("hv_battery_kw", "")),
        "hv_battery_capacity": float_or_none(csv_row.get("hv_battery_capacity", "")),
        "hv_battery_range": float_or_none(csv_row.get("hv_battery_range", "")),
        "hv_battery_max_range": float_or_none(csv_row.get("hv_battery_max_range", "")),
        "hv_battery_temperature": float_or_none(csv_row.get("hv_battery_temperature", "")),
        "lv_battery_level": float_or_none(csv_row.get("lv_battery_level", "")),
        "lv_battery_voltage": float_or_none(csv_row.get("lv_battery_voltage", "")),
        "motor_voltage": float_or_none(csv_row.get("motor_voltage", "")),
        "motor_amperage": float_or_none(csv_row.get("motor_amperage", "")),
        "motor_kw": float_or_none(csv_row.get("motor_kw", "")),
        "performance_status": str_or_none(csv_row.get("performance_status", "")),
        "device_id": device_id,
        "source_system": SOURCE_SYSTEM,
    }
    if db_row["recorded_at"] is None:
        return None
    db_row["original_timestamp"] = db_row["recorded_at"]
    return db_row


# ---------------------------------------------------------------------------
# Charging session transform
# ---------------------------------------------------------------------------

def transform_session_row(csv_row: dict, device_id: str) -> Optional[dict]:
    """Transform a session CSV row to a DB row that mirrors HASS-ingested fields.

    We intentionally leave null every column that hass_processor.handle_energy_transfer
    does NOT populate, so seeded sessions look exactly like real HASS data:

      * cost / cost_without_overrides   (energytransferlogentry has no cost)
      * is_free                          (not in HA payload)
      * location_type                    (HASS leaves session.location_type null;
                                          location lookup infers type on the
                                          locations row, not on the session)
      * charging_voltage / charging_amperage  (HA exposes only weightedAverage kW)
      * evse_voltage / evse_amperage / evse_kw / evse_energy_kwh /
        evse_max_power_kw / evse_source (no charger-side telemetry in HA)

    Everything kept below maps 1:1 to a field the HASS handler fills in.
    """
    session_start = parse_timestamp(csv_row.get("session_start_utc", ""))
    energy_kwh = float_or_none(csv_row.get("energy_kwh", ""))

    if session_start is None and energy_kwh is None:
        return None

    session_id = parse_uuid(csv_row.get("session_id", ""))
    if session_id is None:
        # Generate deterministic UUID
        loc = csv_row.get("location_name", "")
        key = f"{session_start.isoformat() if session_start else ''}|{loc}|{energy_kwh or ''}"
        session_id = uuid.UUID(bytes=hashlib.md5(key.encode()).digest())

    db_row = {
        "session_id": session_id,
        "device_id": device_id,
        "charge_type": str_or_none(csv_row.get("charge_type", "")),
        "location_name": str_or_none(csv_row.get("location_name", "")),
        "charging_kw": float_or_none(csv_row.get("charging_kw", "")),
        "session_start_utc": session_start,
        "session_end_utc": parse_timestamp(csv_row.get("session_end_utc", "")),
        "recorded_at": parse_timestamp(csv_row.get("recorded_at", "")),
        "charge_duration_seconds": float_or_none(csv_row.get("charge_duration_seconds", "")),
        "start_soc": float_or_none(csv_row.get("start_soc", "")),
        "end_soc": float_or_none(csv_row.get("end_soc", "")),
        "energy_kwh": energy_kwh,
        "is_complete": parse_bool(csv_row.get("is_complete", "True")),
        "location_id": int_or_none(csv_row.get("location_id", "")),
        "address": str_or_none(csv_row.get("location_address", "")),
        "latitude": float_or_none(csv_row.get("latitude", "")),
        "longitude": float_or_none(csv_row.get("longitude", "")),
        "max_power": float_or_none(csv_row.get("max_power", "")),
        "min_power": float_or_none(csv_row.get("min_power", "")),
        # CSV stores miles; convert to km for metric-canonical storage.
        # hass_processor normalizes totalDistanceAdded the same way.
        "distance_added": _mi_to_km(float_or_none(csv_row.get("miles_added", ""))),
        "source_system": SOURCE_SYSTEM,
    }
    return db_row


# ---------------------------------------------------------------------------
# Trip metrics transform
# ---------------------------------------------------------------------------

def transform_trip_row(csv_row: dict, device_id: str) -> Optional[dict]:
    """Transform a trip CSV row to metric-base DB row.

    Seed CSV is generated in US units (mi, °F, mi/kWh); the schema stores
    metric base units matching what hass_processor writes, so convert on
    load. Fields HASS never populates (e.g. brake_torque) are left unset so
    the seed accurately simulates HASS-ingested data.
    """
    start_time = parse_timestamp(csv_row.get("start_time", ""))
    end_time = parse_timestamp(csv_row.get("end_time", ""))

    if start_time is None and end_time is None:
        return None

    # CSV → metric base conversions
    distance_km = _mi_to_km(float_or_none(csv_row.get("distance", "")))
    range_regen_km = _mi_to_km(float_or_none(csv_row.get("range_regenerated", "")))
    ambient_c = _f_to_c(float_or_none(csv_row.get("ambient_temp", "")))
    cabin_c = _f_to_c(float_or_none(csv_row.get("cabin_temp", "")))
    outside_c = _f_to_c(float_or_none(csv_row.get("outside_air_temp", "")))

    # efficiency (mi/kWh) → km/kWh: multiply by MI_TO_KM
    eff_raw = float_or_none(csv_row.get("efficiency", ""))
    efficiency_kmkwh = round(eff_raw * _MI_TO_KM, 4) if eff_raw is not None else None
    elec_eff_raw = float_or_none(csv_row.get("electrical_efficiency", ""))
    electrical_efficiency_kmkwh = (
        round(elec_eff_raw * _MI_TO_KM, 4) if elec_eff_raw is not None else None
    )

    db_row = {
        "device_id": device_id,
        "start_time": start_time,
        "end_time": end_time,
        "recorded_at": parse_timestamp(csv_row.get("recorded_at", "")) or end_time,
        # Metric-base values
        "distance": distance_km,
        "duration": float_or_none(csv_row.get("duration", "")),
        "energy_consumed": float_or_none(csv_row.get("energy_consumed", "")),
        "efficiency": efficiency_kmkwh,
        "range_regenerated": range_regen_km,
        "ambient_temp": ambient_c,
        "cabin_temp": cabin_c,
        "outside_air_temp": outside_c,
        "driving_score": float_or_none(csv_row.get("driving_score", "")),
        "speed_score": float_or_none(csv_row.get("speed_score", "")),
        "acceleration_score": float_or_none(csv_row.get("acceleration_score", "")),
        "deceleration_score": float_or_none(csv_row.get("deceleration_score", "")),
        "electrical_efficiency": electrical_efficiency_kmkwh,
        # brake_torque: HASS populates it on vehicle_status snapshots, NOT on
        # trip_metrics (FordPass doesn't expose a trip-level brake torque). Leave
        # null so seeded trips match HASS reality.
        "is_complete": parse_bool(csv_row.get("is_complete", "True")),
        "source_system": SOURCE_SYSTEM,
    }
    if end_time:
        db_row["original_timestamp"] = end_time
    return db_row


# Session columns to update on upsert conflict — kept in sync with the
# HASS-compatible subset in transform_session_row (no cost/is_free/EVSE/etc).
SESSION_UPDATABLE = [
    "device_id", "charge_type", "location_name",
    "session_start_utc", "session_end_utc", "charge_duration_seconds",
    "energy_kwh", "charging_kw", "max_power", "min_power", "start_soc", "end_soc",
    "distance_added",
    "is_complete", "recorded_at", "source_system",
    "location_id", "address", "latitude", "longitude",
]


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------

async def seed_vehicle(device_id: str, dry_run: bool) -> None:
    """Create or update the sample vehicle record."""
    print(f"\n{'='*60}")
    print("  SAMPLE VEHICLE")
    print(f"{'='*60}")

    vehicle_data = {**SAMPLE_VEHICLE, "device_id": device_id}
    print(f"  {vehicle_data['year']} {vehicle_data['make']} {vehicle_data['model']} {vehicle_data['trim']}")
    print(f"  VIN: {vehicle_data['vin']}")
    print(f"  Device ID: {device_id}")
    print(f"  Battery: {vehicle_data['battery_capacity_kwh']} kWh")

    if dry_run:
        print("  [DRY RUN] Would create/update vehicle")
        return

    async with AsyncSessionLocal() as session:
        # Check if vehicle already exists by device_id
        result = await session.execute(
            select(EVVehicle).where(EVVehicle.device_id == device_id)
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing vehicle
            for key, value in vehicle_data.items():
                if key != "device_id":
                    setattr(existing, key, value)
            print(f"  Updated existing vehicle (id={existing.id})")
        else:
            # Check for VIN conflict (different device_id but same VIN)
            vin_result = await session.execute(
                select(EVVehicle).where(EVVehicle.vin == vehicle_data["vin"])
            )
            vin_existing = vin_result.scalar_one_or_none()
            if vin_existing:
                # Update the existing VIN record to use our device_id
                for key, value in vehicle_data.items():
                    setattr(vin_existing, key, value)
                print(f"  Updated existing vehicle with matching VIN (id={vin_existing.id})")
            else:
                vehicle = EVVehicle(**vehicle_data)
                session.add(vehicle)
                print("  Created new vehicle")

        try:
            await session.commit()
        except IntegrityError as e:
            await session.rollback()
            print(f"  WARNING: Could not create vehicle: {e}")

    # Set as active vehicle
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EVVehicle).where(EVVehicle.device_id == device_id)
        )
        vehicle = result.scalar_one_or_none()
        if vehicle:
            from web.queries.settings import set_app_setting
            await set_app_setting(session, "active_vehicle_id", str(vehicle.id))
            print(f"  Set as active vehicle (id={vehicle.id})")


async def seed_battery(device_id: str, csv_path: str, dry_run: bool) -> int:
    print(f"\n{'='*60}")
    print("  BATTERY STATUS")
    print(f"{'='*60}")
    print(f"  CSV: {csv_path}")

    with open(csv_path, newline="", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))
    print(f"  Loaded {len(raw_rows)} rows")

    transformed = []
    for raw_row in raw_rows:
        db_row = transform_battery_row(raw_row, device_id)
        if db_row:
            transformed.append(db_row)
    print(f"  Transformed {len(transformed)} rows")

    if dry_run:
        print(f"  [DRY RUN] Would insert {len(transformed)} rows")
        return len(transformed)

    batch_size = 500
    async with AsyncSessionLocal() as session:
        # Clear existing sample data
        result = await session.execute(
            text("DELETE FROM ev_battery_status WHERE source_system = :src AND device_id = :did"),
            {"src": SOURCE_SYSTEM, "did": device_id},
        )
        print(f"  Cleared {result.rowcount} existing sample rows")

        for i in range(0, len(transformed), batch_size):
            batch = transformed[i : i + batch_size]
            await session.execute(pg_insert(EVBatteryStatus).values(batch))

        await session.commit()
    print(f"  Inserted {len(transformed)} rows")
    return len(transformed)


async def seed_sessions(device_id: str, csv_path: str, dry_run: bool) -> int:
    print(f"\n{'='*60}")
    print("  CHARGING SESSIONS")
    print(f"{'='*60}")
    print(f"  CSV: {csv_path}")

    with open(csv_path, newline="", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))
    print(f"  Loaded {len(raw_rows)} rows")

    transformed = []
    for raw_row in raw_rows:
        db_row = transform_session_row(raw_row, device_id)
        if db_row:
            transformed.append(db_row)
    print(f"  Transformed {len(transformed)} rows")

    if dry_run:
        print(f"  [DRY RUN] Would upsert {len(transformed)} rows")
        return len(transformed)

    async with AsyncSessionLocal() as session:
        # Delete existing sample rows before re-insert. An upsert would keep
        # stale values in columns we now deliberately leave unset (cost, is_free,
        # location_type, evse_*), masking the "HASS-only" shape we want to
        # simulate. Match the seed_battery / seed_trips delete-and-insert
        # pattern instead.
        result = await session.execute(
            text("DELETE FROM ev_charging_session WHERE source_system = :src AND device_id = :did"),
            {"src": SOURCE_SYSTEM, "did": device_id},
        )
        print(f"  Cleared {result.rowcount} existing sample rows")

        batch_size = 500
        for i in range(0, len(transformed), batch_size):
            batch = transformed[i : i + batch_size]
            await session.execute(pg_insert(EVChargingSession).values(batch))
        await session.commit()
    print(f"  Inserted {len(transformed)} rows")
    return len(transformed)


async def seed_trips(device_id: str, csv_path: str, dry_run: bool) -> int:
    print(f"\n{'='*60}")
    print("  TRIP METRICS")
    print(f"{'='*60}")
    print(f"  CSV: {csv_path}")

    with open(csv_path, newline="", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))
    print(f"  Loaded {len(raw_rows)} rows")

    transformed = []
    for raw_row in raw_rows:
        db_row = transform_trip_row(raw_row, device_id)
        if db_row:
            transformed.append(db_row)
    print(f"  Transformed {len(transformed)} rows")

    if dry_run:
        print(f"  [DRY RUN] Would insert {len(transformed)} rows")
        return len(transformed)

    batch_size = 500
    async with AsyncSessionLocal() as session:
        # Clear existing sample data
        result = await session.execute(
            text("DELETE FROM ev_trip_metrics WHERE source_system = :src AND device_id = :did"),
            {"src": SOURCE_SYSTEM, "did": device_id},
        )
        print(f"  Cleared {result.rowcount} existing sample rows")

        for i in range(0, len(transformed), batch_size):
            batch = transformed[i : i + batch_size]
            await session.execute(pg_insert(EVTripMetrics).values(batch))

        await session.commit()
    print(f"  Inserted {len(transformed)} rows")
    return len(transformed)


async def verify(device_id: str):
    print(f"\n{'='*60}")
    print("  VERIFICATION")
    print(f"{'='*60}")

    async with AsyncSessionLocal() as session:
        # Battery stats
        result = await session.execute(
            text("""
                SELECT COUNT(*) AS total,
                       MIN(recorded_at) AS earliest,
                       MAX(recorded_at) AS latest,
                       ROUND(AVG(hv_battery_soc)::numeric, 1) AS avg_soc,
                       COUNT(*) FILTER (WHERE hv_battery_kw < -1) AS charging,
                       COUNT(*) FILTER (WHERE motor_kw > 1) AS driving
                FROM ev_battery_status WHERE device_id = :did
            """),
            {"did": device_id},
        )
        b = result.fetchone()

        # Session stats — no cost/location_type (HASS-parity seed doesn't set them)
        result = await session.execute(
            text("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE charge_type = 'AC') AS ac,
                       COUNT(*) FILTER (WHERE charge_type = 'DC') AS dc,
                       ROUND(SUM(energy_kwh)::numeric, 1) AS total_kwh,
                       COUNT(*) FILTER (WHERE location_name ILIKE 'Home') AS home,
                       COUNT(*) FILTER (WHERE location_name ILIKE 'Work') AS work
                FROM ev_charging_session WHERE device_id = :did
            """),
            {"did": device_id},
        )
        s = result.fetchone()

        # Trip stats — values are metric base (km, km/kWh) after the
        # transform_trip_row conversion.
        result = await session.execute(
            text("""
                SELECT COUNT(*) AS total,
                       MIN(start_time) AS earliest,
                       MAX(end_time) AS latest,
                       ROUND(SUM(distance)::numeric, 1) AS total_km,
                       ROUND(AVG(efficiency)::numeric, 2) AS avg_efficiency,
                       ROUND(AVG(driving_score)::numeric, 1) AS avg_score,
                       ROUND(SUM(energy_consumed)::numeric, 1) AS total_energy,
                       ROUND(MIN(ambient_temp)::numeric, 1) AS min_ambient_c,
                       ROUND(MAX(ambient_temp)::numeric, 1) AS max_ambient_c
                FROM ev_trip_metrics WHERE device_id = :did
            """),
            {"did": device_id},
        )
        t = result.fetchone()

    print("\n  Battery Status:")
    print(f"    Total rows:   {b.total}")
    print(f"    Date range:   {str(b.earliest)[:10]} to {str(b.latest)[:10]}")
    print(f"    Avg SOC:      {b.avg_soc}%")
    print(f"    Charging:     {b.charging} snapshots")
    print(f"    Driving:      {b.driving} snapshots")

    print("\n  Charging Sessions:")
    print(f"    Total:        {s.total}")
    print(f"    AC: {s.ac} | DC: {s.dc}")
    print(f"    Home: {s.home} | Work: {s.work} | Other: {s.total - s.home - s.work}")
    print(f"    Total energy: {s.total_kwh} kWh")

    print("\n  Trip Metrics:")
    print(f"    Total trips:  {t.total}")
    if t.total > 0:
        print(f"    Date range:   {str(t.earliest)[:10]} to {str(t.latest)[:10]}")
        print(f"    Total km:     {t.total_km}")
        print(f"    Avg eff:      {t.avg_efficiency} km/kWh  ({(float(t.avg_efficiency) * 0.621371):.2f} mi/kWh)")
        print(f"    Avg score:    {t.avg_score}")
        print(f"    Total energy: {t.total_energy} kWh")
        print(f"    Ambient range: {t.min_ambient_c}°C to {t.max_ambient_c}°C  "
              f"({(float(t.min_ambient_c) * 9/5 + 32):.1f}°F to "
              f"{(float(t.max_ambient_c) * 9/5 + 32):.1f}°F)")


async def seed(args: argparse.Namespace):
    device_id = args.device_id
    dry_run = args.dry_run
    data_dir = Path("data")

    battery_csv = str(data_dir / "battery_status_sample.csv")
    sessions_csv = str(data_dir / "charging_sessions_sample.csv")
    trips_csv = str(data_dir / "trip_metrics_sample.csv")

    # Determine which data types to seed
    only_flags = [args.battery_only, args.sessions_only, args.trips_only]
    seed_all = not any(only_flags)

    print(f"\n  Device ID: {device_id}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")

    # Always create/update the sample vehicle first
    await seed_vehicle(device_id, dry_run)

    if seed_all or args.battery_only:
        await seed_battery(device_id, battery_csv, dry_run)

    if seed_all or args.sessions_only:
        await seed_sessions(device_id, sessions_csv, dry_run)

    if seed_all or args.trips_only:
        await seed_trips(device_id, trips_csv, dry_run)

    if not dry_run:
        await verify(device_id)

    print("\n  Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Seed correlated battery + charging sample data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python scripts/seed_sample.py
  uv run python scripts/seed_sample.py --dry-run
  uv run python scripts/seed_sample.py --sessions-only
  uv run python scripts/seed_sample.py --trips-only
  uv run python scripts/seed_sample.py --device-id CUSTOM_VIN
        """,
    )
    parser.add_argument("--device-id", default="1FT8W3ED5LFB0D19", required=False, help="Device ID for all rows. Defaults to sample Vin: 1FT8W3ED5LFB0D19")
    parser.add_argument("--dry-run", action="store_true", help="Transform but don't write")
    parser.add_argument("--battery-only", action="store_true", help="Only seed battery status")
    parser.add_argument("--sessions-only", action="store_true", help="Only seed charging sessions")
    parser.add_argument("--trips-only", action="store_true", help="Only seed trip metrics")
    args = parser.parse_args()
    asyncio.run(seed(args))


if __name__ == "__main__":
    main()
