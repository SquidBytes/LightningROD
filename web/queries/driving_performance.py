"""Driving performance query layer.

Aggregates trip-side metrics for the /driving/performance page:
- Total distance, average driving efficiency, trip count (from EVTripMetrics)
- Regen recovery total (delegated to energy.query_regen_summary)
- Efficiency trend data (passthrough of trips.query_efficiency_trend)

Returns metric-base values. The route handler applies distance_factor conversion
before passing to templates / chart builders.

Wave 2 (Plan 25-03) will replace the `query_temperature_correlation` and
`query_regen_per_trip` stubs with real implementations.
"""

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.queries.energy import query_regen_summary
from web.queries.trips import build_trip_time_filter, query_efficiency_trend


async def query_driving_performance_summary(
    db: AsyncSession,
    time_range: str = "all",
    device_id: Optional[str] = None,
) -> dict:
    """Aggregate driving-side metrics for /driving/performance page.

    Returns metric-base values. The route handler applies distance_factor
    conversion before passing to templates.

    Returns a dict with:
    - total_distance (km, metric base)
    - total_energy (kWh)
    - avg_driving_efficiency (km/kWh, metric base) — None when total_energy is 0/None
    - total_regen (km, metric base — from query_regen_summary)
    - range_recovered (km, metric base — alias of total_regen for the tile)
    - trip_count (prefer regen trip_count, fall back to EVTripMetrics count)
    - efficiency_trend (passthrough from query_efficiency_trend)
    - temperature_correlation_data (Wave 2 placeholder, None)
    - regen_per_trip_data (Wave 2 placeholder, None)
    """
    # 1. Regen summary (can return None when no regen data exists)
    regen = await query_regen_summary(
        db, time_range=time_range, device_id=device_id
    )

    # 2. Distance + energy + trip count from EVTripMetrics
    stmt = select(
        func.sum(EVTripMetrics.distance),
        func.sum(EVTripMetrics.energy_consumed),
        func.count(EVTripMetrics.id),
    )

    cutoff = build_trip_time_filter(time_range)
    if cutoff is not None:
        stmt = stmt.where(EVTripMetrics.end_time >= cutoff)
    if device_id:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)

    result = await db.execute(stmt)
    row = result.one()
    total_distance = float(row[0]) if row[0] is not None else None
    total_energy = float(row[1]) if row[1] is not None else None
    trip_count_trips = int(row[2]) if row[2] is not None else 0

    # 3. Derive avg driving efficiency (None-safe)
    if total_distance is not None and total_energy is not None and total_energy > 0:
        avg_eff = total_distance / total_energy
    else:
        avg_eff = None

    # 4. Efficiency trend passthrough (used by Driving Efficiency chart)
    trend_data = await query_efficiency_trend(
        db, time_range=time_range, device_id=device_id
    )

    return {
        "total_distance": total_distance,
        "total_energy": total_energy,
        "avg_driving_efficiency": avg_eff,
        "total_regen": regen.get("regen_total") if regen else None,
        "range_recovered": regen.get("regen_total") if regen else None,
        "trip_count": (regen.get("trip_count") if regen else 0) or trip_count_trips or 0,
        "efficiency_trend": trend_data,
        # Wave 2 placeholders — filled by Plan 25-03
        "temperature_correlation_data": None,
        "regen_per_trip_data": None,
    }


async def query_temperature_correlation(
    db: AsyncSession,
    time_range: str = "all",
    device_id: Optional[str] = None,
) -> list[dict]:
    """Wave 2 stub — returns [] until Plan 25-03 fills in.

    When implemented: must filter `EVTripMetrics.ambient_temp IS NOT NULL`
    and return rows with {ambient_temp, efficiency, distance} tuples for the
    temperature-vs-efficiency scatter chart.
    """
    return []


async def query_regen_per_trip(
    db: AsyncSession,
    time_range: str = "all",
    device_id: Optional[str] = None,
) -> list[dict]:
    """Wave 2 stub — returns [] until Plan 25-03 fills in.

    When implemented: returns per-trip regen-kWh and distance values for the
    Regen Recovery dual-axis bar chart.
    """
    return []
