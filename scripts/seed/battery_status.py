"""Seed module: EV battery status readings correlated to charging sessions."""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.vehicle import EVVehicle
from scripts.seed.base import ContractDrivenSeeder, load_declared_contracts

_DEMO_VIN = "1FT6W1EV0NWG00000"
_IDEMPOTENCY_THRESHOLD = 120

# Realistic range (km) for the F-150 Lightning Standard Range at 100% SoC
_MAX_RANGE_KM = 370.0

logger = logging.getLogger(__name__)


def _soc_to_range(soc: float) -> float:
    """Estimate range from SoC — simple linear approximation."""
    return round((_soc_to_range_raw(soc)), 1)


def _soc_to_range_raw(soc: float) -> float:
    return _MAX_RANGE_KM * (soc / 100.0)


async def seed(db: AsyncSession) -> int:
    """Insert ≥120 EVBatteryStatus rows correlated to T9 charging sessions.

    Pattern: 3 readings per session (start / midpoint / end) plus ~30
    between-session parked/driving readings every ~12 hours.

    Idempotent: returns 0 if ≥120 rows already exist for the demo vehicle.
    """
    # --- resolve demo vehicle ------------------------------------------------
    vehicle = (
        await db.execute(select(EVVehicle).where(EVVehicle.vin == _DEMO_VIN))
    ).scalar_one_or_none()
    if vehicle is None:
        raise RuntimeError(
            f"Demo vehicle with VIN {_DEMO_VIN!r} not found. "
            "Run the vehicle seed module first."
        )
    device_id = vehicle.device_id

    # --- idempotency check ---------------------------------------------------
    existing_count: int = (
        await db.execute(
            select(func.count()).where(EVBatteryStatus.device_id == device_id)
        )
    ).scalar_one()
    if existing_count >= _IDEMPOTENCY_THRESHOLD:
        logger.info(
            "battery_status: %d rows already exist for device %s — skipping",
            existing_count,
            device_id,
        )
        return 0

    # --- load charging sessions ----------------------------------------------
    sessions: list[EVChargingSession] = list(
        (
            await db.execute(
                select(EVChargingSession)
                .where(EVChargingSession.device_id == device_id)
                .order_by(EVChargingSession.session_start_utc)
            )
        )
        .scalars()
        .all()
    )
    if not sessions:
        raise RuntimeError(
            "No EVChargingSession rows found for demo vehicle. "
            "Run the charging_sessions seed module (T9) before battery_status (T10)."
        )

    # --- set up deterministic RNG and ContractDrivenSeeder -------------------
    rng = random.Random(42)
    seeder = ContractDrivenSeeder(declared=load_declared_contracts(), rng=rng)

    rows: list[EVBatteryStatus] = []

    # --- helper to build a single EVBatteryStatus row -----------------------
    def _make_row(
        recorded_at: datetime,
        soc: float,
        temp: float,
    ) -> EVBatteryStatus:
        hv_range = round(_soc_to_range_raw(soc), 1)
        voltage = round(rng.uniform(350.0, 420.0), 1)
        amperage = round(rng.uniform(-5.0, 5.0), 2)  # near-zero when parked
        lv_level = round(rng.uniform(75.0, 99.0), 1)
        lv_voltage = round(rng.uniform(12.1, 14.8), 2)
        return EVBatteryStatus(
            device_id=device_id,
            recorded_at=recorded_at,
            hv_battery_soc=round(soc, 1),
            hv_battery_actual_soc=round(soc + rng.uniform(-1.0, 1.0), 1),
            hv_battery_range=hv_range,
            hv_battery_max_range=_MAX_RANGE_KM,
            hv_battery_voltage=voltage,
            hv_battery_amperage=amperage,
            hv_battery_kw=round(voltage * amperage / 1000.0, 3),
            hv_battery_temperature=temp,
            lv_battery_level=lv_level,
            lv_battery_voltage=lv_voltage,
            source_system="seed",
            ingest_schema_version=2,
        )

    # --- 3 readings per charging session (start / midpoint / end) -----------
    for session in sessions:
        start_ts = session.session_start_utc
        end_ts = session.session_end_utc

        # Fall back gracefully if timestamps or SoC are missing
        if start_ts is None or end_ts is None:
            continue
        if start_ts.tzinfo is None:
            start_ts = start_ts.replace(tzinfo=UTC)
        if end_ts.tzinfo is None:
            end_ts = end_ts.replace(tzinfo=UTC)

        soc_start = float(session.start_soc) if session.start_soc is not None else rng.uniform(15.0, 45.0)
        soc_end = float(session.end_soc) if session.end_soc is not None else rng.uniform(60.0, 95.0)
        soc_mid = (soc_start + soc_end) / 2.0

        # Temperatures: start cool, peak at midpoint, end slightly cooler
        temp_start: float = float(seeder.value_for("ev_battery_status", "hv_battery_temperature"))
        temp_mid: float = round(temp_start + rng.uniform(2.0, 6.0), 1)
        temp_end: float = round(temp_start + rng.uniform(1.0, 3.0), 1)

        mid_ts = start_ts + (end_ts - start_ts) / 2

        rows.append(_make_row(start_ts, soc_start, temp_start))
        rows.append(_make_row(mid_ts, soc_mid, temp_mid))
        rows.append(_make_row(end_ts, soc_end, temp_end))

    # --- ~30 between-session parked/driving readings -------------------------
    # Walk the full timeline and add a reading every ~12 hours where there
    # is no nearby session anchor.
    if sessions:
        timeline_start: datetime = sessions[0].session_start_utc  # type: ignore[assignment]
        timeline_end: datetime = sessions[-1].session_end_utc or sessions[-1].session_start_utc  # type: ignore[assignment]
        if timeline_start.tzinfo is None:
            timeline_start = timeline_start.replace(tzinfo=UTC)
        if timeline_end is not None and timeline_end.tzinfo is None:
            timeline_end = timeline_end.replace(tzinfo=UTC)

        # Collect all session-anchor timestamps for proximity check
        anchor_timestamps: list[datetime] = []
        for s in sessions:
            for ts in (s.session_start_utc, s.session_end_utc):
                if ts is not None:
                    t = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
                    anchor_timestamps.append(t)

        interval = timedelta(hours=12)
        # Jitter each tick by up to ±30 minutes for realism
        cursor = timeline_start + timedelta(hours=rng.uniform(1, 6))
        between_count = 0

        while cursor < timeline_end and between_count < 32:
            # Skip if within 30 minutes of any anchor
            too_close = any(abs((cursor - a).total_seconds()) < 1800 for a in anchor_timestamps)
            if not too_close:
                # Parked state: gently drifting SoC (self-discharge ~0.3% per 12 hr)
                soc_parked = round(rng.uniform(20.0, 90.0), 1)
                temp_parked: float = float(seeder.value_for("ev_battery_status", "hv_battery_temperature"))
                rows.append(_make_row(cursor, soc_parked, temp_parked))
                between_count += 1

            cursor += interval + timedelta(minutes=rng.uniform(-15, 15))

    # --- insert all rows at once --------------------------------------------
    db.add_all(rows)
    await db.flush()

    logger.info(
        "battery_status: inserted %d rows for device %s (%d session anchors + %d between-session)",
        len(rows),
        device_id,
        len(sessions) * 3,
        len(rows) - len(sessions) * 3,
    )
    return len(rows)
