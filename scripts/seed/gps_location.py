"""Seed module: EVLocation GPS time-series — ≥1000 points along T11 trips.

Generates one GPS point per minute for each EVTripMetrics trip in the 90-day
demo window. Uses a random walk from Denver (39.7392, -104.9903) as base,
constrained to ±0.5° in both axes. Heading and speed are correlated to the
step distance. Supplemental standalone points are added if trip coverage
falls below 1000 total rows.

Idempotent: if ≥1000 ev_location rows exist for the demo device_id, returns 0.

Deterministic: random.Random(42).
"""

from __future__ import annotations

import logging
import math
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.location import EVLocation
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle import EVVehicle

logger = logging.getLogger(__name__)

_DEMO_VIN = "1FT6W1EV0NWG00000"
_IDEMPOTENCY_THRESHOLD = 1000
_MIN_TOTAL_ROWS = 1000

# Denver, CO — public landmark, not a residence
_BASE_LAT = 39.7392
_BASE_LON = -104.9903

# Coordinate drift constraints
_MAX_STEP_DEG = 0.01       # ~1.1 km per minute at full speed
_MAX_DRIFT_DEG = 0.5       # stay within ±0.5° of base


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _generate_trip_points(
    rng: random.Random,
    device_id: str,
    start_time: datetime,
    end_time: datetime,
    start_lat: float,
    start_lon: float,
) -> tuple[list[EVLocation], float, float]:
    """Generate one EVLocation per minute between start_time and end_time.

    Returns (rows, final_lat, final_lon) so trips can chain starting positions.
    """
    duration_s = (end_time - start_time).total_seconds()
    num_points = max(1, math.floor(duration_s / 60))

    rows: list[EVLocation] = []
    lat = start_lat
    lon = start_lon
    heading = rng.uniform(0, 360)

    for i in range(num_points):
        recorded_at = start_time + timedelta(minutes=i)

        # Random walk: step size 0–_MAX_STEP_DEG; heading drifts ±30°/min
        step = rng.uniform(0, _MAX_STEP_DEG)
        heading = (heading + rng.uniform(-30, 30)) % 360

        heading_rad = math.radians(heading)
        d_lat = step * math.cos(heading_rad)
        d_lon = step * math.sin(heading_rad)

        lat = _clamp(lat + d_lat, _BASE_LAT - _MAX_DRIFT_DEG, _BASE_LAT + _MAX_DRIFT_DEG)
        lon = _clamp(lon + d_lon, _BASE_LON - _MAX_DRIFT_DEG, _BASE_LON + _MAX_DRIFT_DEG)

        # Altitude: Denver ~1609 m with ±20 m noise
        altitude = round(1609.0 + rng.uniform(-20, 20), 1)

        # Compass direction from heading
        compass_direction = _heading_to_compass(heading)

        rows.append(
            EVLocation(
                device_id=device_id,
                recorded_at=recorded_at,
                latitude=round(lat, 6),
                longitude=round(lon, 6),
                altitude=altitude,
                compass_direction=compass_direction,
                gps_accuracy=round(rng.uniform(2.0, 10.0), 1),
                location_type="driving",
                source_system="seed",
                original_timestamp=recorded_at,
            )
        )

    return rows, lat, lon


def _heading_to_compass(heading: float) -> str:
    """Convert 0–360° heading to an 8-point compass direction string."""
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(heading / 45) % 8
    return directions[idx]


async def seed(db: AsyncSession) -> int:
    """Insert ≥1000 EVLocation GPS rows for the demo vehicle's trips.

    Returns the number of rows inserted (0 if already seeded).
    """
    # Resolve demo vehicle
    vehicle = (
        await db.execute(select(EVVehicle).where(EVVehicle.vin == _DEMO_VIN))
    ).scalar_one_or_none()
    if vehicle is None:
        raise RuntimeError(
            f"Demo vehicle VIN={_DEMO_VIN!r} not found — run vehicle seed first."
        )

    device_id = vehicle.device_id

    # Idempotency check
    existing_count = (
        await db.execute(
            select(func.count())
            .select_from(EVLocation)
            .where(EVLocation.device_id == device_id)
        )
    ).scalar_one()

    if existing_count >= _IDEMPOTENCY_THRESHOLD:
        logger.info(
            "gps_location: %d rows already exist for device_id=%r — skipping",
            existing_count,
            device_id,
        )
        return 0

    rng = random.Random(42)

    # Load all trips for this device, ordered by start_time
    trips_result = await db.execute(
        select(EVTripMetrics)
        .where(EVTripMetrics.device_id == device_id)
        .order_by(EVTripMetrics.start_time)
    )
    trips = list(trips_result.scalars().all())

    all_rows: list[EVLocation] = []
    lat = _BASE_LAT
    lon = _BASE_LON

    for trip in trips:
        if trip.start_time is None or trip.end_time is None:
            continue
        trip_rows, lat, lon = _generate_trip_points(
            rng=rng,
            device_id=device_id,
            start_time=trip.start_time,
            end_time=trip.end_time,
            start_lat=lat,
            start_lon=lon,
        )
        all_rows.extend(trip_rows)

    # Supplement if we're still below the minimum threshold
    if len(all_rows) < _MIN_TOTAL_ROWS:
        supplement_needed = _MIN_TOTAL_ROWS - len(all_rows)
        logger.info(
            "gps_location: %d trip points generated; supplementing with %d standalone points",
            len(all_rows),
            supplement_needed,
        )
        # Generate a synthetic supplemental trip block
        now = datetime.now(UTC)
        supp_start = now - timedelta(days=1)
        supp_end = supp_start + timedelta(minutes=supplement_needed)
        supp_rows, lat, lon = _generate_trip_points(
            rng=rng,
            device_id=device_id,
            start_time=supp_start,
            end_time=supp_end,
            start_lat=lat,
            start_lon=lon,
        )
        all_rows.extend(supp_rows)

    db.add_all(all_rows)
    await db.flush()

    logger.info(
        "gps_location: inserted %d rows for device_id=%r",
        len(all_rows),
        device_id,
    )
    return len(all_rows)
