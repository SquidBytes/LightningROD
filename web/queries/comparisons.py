"""EV-to-ICE comparison calculations and chart helpers."""


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.ice_vehicle import IceVehicle
from db.models.reference import GasPriceHistory
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle import EVVehicle
from db.models.vehicle_status import EVVehicleStatus
from web.queries.costs import (
    build_time_filter,
    calculate_monthly_fees_in_range,
    compute_session_cost,
    get_networks_by_name,
)
from web.queries.settings import get_all_subscriptions_by_network
from web.queries.time_window import resolve_time_window, window_clause
from web.unit_system import GAL_PER_LITER, LITER_PER_GAL, MI_PER_KM


def _find_gas_price(
    prices: list[GasPriceHistory], year: int, month: int
) -> tuple[float | None, float | None]:
    """Find the gas price entry for (year, month) or the nearest earlier month.

    Prices must be sorted by (year DESC, month DESC). Returns
    (station_price, average_price) in $/gal, defaulting to (3.50, 3.50) when
    there are no entries. Storage is $/L; converted at the read boundary.
    """
    for entry in prices:
        if (entry.year, entry.month) <= (year, month):
            station = float(entry.station_price) if entry.station_price is not None else None
            average = float(entry.average_price) if entry.average_price is not None else None
            station_per_gal = station * LITER_PER_GAL if station is not None else None
            average_per_gal = average * LITER_PER_GAL if average is not None else None
            return (station_per_gal, average_per_gal)
    # No entry found — use default ($/gal)
    return (3.50, 3.50)


def _empty_gas_result() -> dict:
    """Return a zeroed-out gas comparison result dict."""
    return {
        "ev_total": 0.0,
        "ev_energy": 0.0,
        "ev_fees": 0.0,
        "gas_total_low": 0.0,
        "gas_total_high": 0.0,
        "gas_total_station": 0.0,
        "gas_total_average": 0.0,
        "savings_low": 0.0,
        "savings_high": 0.0,
        "savings_pct_low": 0.0,
        "savings_pct_high": 0.0,
        "session_count": 0,
        "total_distance": 0.0,  # km (metric base)
        "ice_label": None,
        "has_range": False,
        "station_price_min": None,
        "station_price_max": None,
        "average_price_min": None,
        "average_price_max": None,
    }


async def query_gas_comparison(
    db: AsyncSession,
    device_id: str | None = None,
    vehicle: EVVehicle | None = None,
    ice_vehicle: IceVehicle | None = None,
    time_range: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Compare actual EV charging cost to the equivalent gasoline cost.

    Uses date-aware gas price lookup with two price tracks (station and average)
    to produce a savings range. Two gallons-equivalent paths:
    - Primary (distance-based): session.distance_added > 0 and the ICE
      vehicle's fuel_efficiency_l_per_100km is set.
    - Fallback (percentage-based): session.energy_kwh / pack capacity, scaled
      by tank capacity.

    Stored values are metric; gas prices are $/gal, so distance/efficiency are
    converted internally for the cost math.
    """
    # If no ICE vehicle configured, return empty result
    if ice_vehicle is None or not ice_vehicle.fuel_efficiency_l_per_100km:
        return _empty_gas_result()

    ice_l_per_100km = float(ice_vehicle.fuel_efficiency_l_per_100km)
    ice_mpg = 235.215 / ice_l_per_100km if ice_l_per_100km > 0 else None
    battery_kwh = float(vehicle.battery_capacity_kwh) if vehicle and vehicle.battery_capacity_kwh else None
    fuel_tank_liters = float(ice_vehicle.tank_capacity_l) if ice_vehicle.tank_capacity_l else None
    fuel_tank_gal = fuel_tank_liters * GAL_PER_LITER if fuel_tank_liters else None

    price_result = await db.execute(
        select(GasPriceHistory).order_by(
            GasPriceHistory.year.desc(), GasPriceHistory.month.desc()
        )
    )
    prices = list(price_result.scalars().all())

    networks_by_name = await get_networks_by_name(db)
    subs_by_network = await get_all_subscriptions_by_network(db)

    stmt = select(EVChargingSession)
    time_filter = build_time_filter(time_range, date_from, date_to)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    ev_energy = 0.0
    gas_total_station = 0.0
    gas_total_average = 0.0
    station_has_data = False
    average_has_data = False
    session_count = 0
    total_distance_km = 0.0
    station_prices_seen: list[float] = []
    average_prices_seen: list[float] = []
    # Session date bounds drive subscription-fee proration for the all-in EV cost.
    range_start_min = None
    range_end_max = None

    for s in sessions:
        cost_info = compute_session_cost(s, networks_by_name)
        if cost_info["display_cost"] is None:
            continue

        gallons = None
        distance_km = float(s.distance_added) if s.distance_added else 0.0
        distance_mi = distance_km * MI_PER_KM

        if distance_mi > 0 and ice_mpg:
            gallons = distance_mi / ice_mpg
        elif (
            s.energy_kwh
            and float(s.energy_kwh) > 0
            and battery_kwh
            and fuel_tank_gal
        ):
            # Fallback: charged fraction of the pack scaled to tank capacity.
            pct = float(s.energy_kwh) / battery_kwh
            gallons = pct * fuel_tank_gal

        if gallons is None:
            continue

        if s.session_start_utc is None:
            continue
        s_year = s.session_start_utc.year
        s_month = s.session_start_utc.month
        station_price, average_price = _find_gas_price(prices, s_year, s_month)

        if station_price is not None:
            gas_total_station += gallons * station_price
            station_has_data = True
            station_prices_seen.append(station_price)
        if average_price is not None:
            gas_total_average += gallons * average_price
            average_has_data = True
            average_prices_seen.append(average_price)

        ev_energy += cost_info["display_cost"]
        session_count += 1
        total_distance_km += distance_km

        session_date = s.session_start_utc.date()
        range_start_min = session_date if range_start_min is None else min(range_start_min, session_date)
        range_end_max = session_date if range_end_max is None else max(range_end_max, session_date)

    if station_has_data and average_has_data:
        gas_total_low = min(gas_total_station, gas_total_average)
        gas_total_high = max(gas_total_station, gas_total_average)
        has_range = gas_total_low != gas_total_high
    elif station_has_data:
        gas_total_low = gas_total_high = gas_total_station
        has_range = False
    elif average_has_data:
        gas_total_low = gas_total_high = gas_total_average
        has_range = False
    else:
        gas_total_low = gas_total_high = 0.0
        has_range = False

    # ev_total carries subscription fees — the gas comparison is all-in cost.
    ev_fees = 0.0
    if range_start_min is not None and range_end_max is not None:
        for periods in subs_by_network.values():
            ev_fees += calculate_monthly_fees_in_range(periods, range_start_min, range_end_max)

    ev_total = ev_energy + ev_fees

    savings_low = gas_total_low - ev_total
    savings_high = gas_total_high - ev_total
    savings_pct_low = (savings_low / gas_total_low * 100) if gas_total_low > 0 else 0.0
    savings_pct_high = (savings_high / gas_total_high * 100) if gas_total_high > 0 else 0.0

    return {
        "ev_total": ev_total,
        "ev_energy": ev_energy,
        "ev_fees": ev_fees,
        "gas_total_low": gas_total_low,
        "gas_total_high": gas_total_high,
        "gas_total_station": gas_total_station,
        "gas_total_average": gas_total_average,
        "savings_low": savings_low,
        "savings_high": savings_high,
        "savings_pct_low": savings_pct_low,
        "savings_pct_high": savings_pct_high,
        "session_count": session_count,
        "total_distance": total_distance_km,  # km, metric base
        "ice_label": ice_vehicle.label,
        "has_range": has_range,
        "station_price_min": min(station_prices_seen) if station_prices_seen else None,
        "station_price_max": max(station_prices_seen) if station_prices_seen else None,
        "average_price_min": min(average_prices_seen) if average_prices_seen else None,
        "average_price_max": max(average_prices_seen) if average_prices_seen else None,
    }


async def _monthly_distance_from_odometer(
    db: AsyncSession, device_id: str | None, start, end
) -> dict[tuple[int, int], float]:
    """Monthly driven km from odometer progression, keyed by (year, month).

    Walks consecutive readings and attributes each positive delta to the month
    of the later reading. Negative deltas (odometer reset / bad reading) are
    skipped. Needs at least two readings to produce any distance.
    """
    stmt = (
        select(EVVehicleStatus.recorded_at, EVVehicleStatus.odometer)
        .where(EVVehicleStatus.odometer.isnot(None))
        .order_by(EVVehicleStatus.recorded_at)
    )
    if device_id:
        stmt = stmt.where(EVVehicleStatus.device_id == device_id)
    clause = window_clause(EVVehicleStatus.recorded_at, start, end)
    if clause is not None:
        stmt = stmt.where(clause)

    rows = (await db.execute(stmt)).all()
    monthly: dict[tuple[int, int], float] = {}
    prev = None
    for recorded_at, odometer in rows:
        odo = float(odometer)
        if prev is not None:
            delta = odo - prev
            if delta > 0:
                key = (recorded_at.year, recorded_at.month)
                monthly[key] = monthly.get(key, 0.0) + delta
        prev = odo
    return monthly


async def _monthly_distance_from_trips(
    db: AsyncSession, device_id: str | None, start, end
) -> dict[tuple[int, int], float]:
    """Monthly driven km summed from trip distances, keyed by (year, month)."""
    stmt = (
        select(EVTripMetrics.end_time, EVTripMetrics.distance)
        .where(EVTripMetrics.distance.isnot(None))
        .where(EVTripMetrics.end_time.isnot(None))
    )
    if device_id:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)
    clause = window_clause(EVTripMetrics.end_time, start, end)
    if clause is not None:
        stmt = stmt.where(clause)

    rows = (await db.execute(stmt)).all()
    monthly: dict[tuple[int, int], float] = {}
    for end_time, distance in rows:
        key = (end_time.year, end_time.month)
        monthly[key] = monthly.get(key, 0.0) + float(distance)
    return monthly


def _empty_distance_result() -> dict:
    return {
        "has_data": False,
        "distance_source": None,
        "total_distance": 0.0,  # km (metric base)
        "ev_total": 0.0,
        "ev_energy": 0.0,
        "ev_fees": 0.0,
        "session_count": 0,
        "gas_total_station": 0.0,
        "gas_total_average": 0.0,
        "gas_total_low": 0.0,
        "gas_total_high": 0.0,
        "savings_low": 0.0,
        "savings_high": 0.0,
        "has_range": False,
        "ice_label": None,
        "ice_l_per_100km": None,
        "station_price_min": None,
        "station_price_max": None,
        "average_price_min": None,
        "average_price_max": None,
    }


async def query_distance_gas_comparison(
    db: AsyncSession,
    device_id: str | None = None,
    ice_vehicle: IceVehicle | None = None,
    time_range: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Compare actual EV spend to fueling the SAME driven distance in the ICE.

    Unlike query_gas_comparison (which prices the charged energy as gas and
    assumes the ICE matches the EV's efficiency on its fallback path), this
    uses miles actually driven and the ICE vehicle's real fuel efficiency:
    monthly driven distance / ICE MPG x that month's historical gas price.

    Distance source: odometer progression (primary), trip-distance sums
    (fallback when no odometer data exists in the window).
    """
    if ice_vehicle is None or not ice_vehicle.fuel_efficiency_l_per_100km:
        return _empty_distance_result()

    ice_l_per_100km = float(ice_vehicle.fuel_efficiency_l_per_100km)
    if ice_l_per_100km <= 0:
        return _empty_distance_result()
    ice_mpg = 235.215 / ice_l_per_100km

    start, end = resolve_time_window(time_range, date_from, date_to)

    monthly_km = await _monthly_distance_from_odometer(db, device_id, start, end)
    distance_source = "odometer"
    if not monthly_km:
        monthly_km = await _monthly_distance_from_trips(db, device_id, start, end)
        distance_source = "trips"
    if not monthly_km:
        return _empty_distance_result()

    price_result = await db.execute(
        select(GasPriceHistory).order_by(
            GasPriceHistory.year.desc(), GasPriceHistory.month.desc()
        )
    )
    prices = list(price_result.scalars().all())

    gas_total_station = 0.0
    gas_total_average = 0.0
    station_prices_seen: list[float] = []
    average_prices_seen: list[float] = []
    total_distance_km = 0.0

    for (year, month), km in sorted(monthly_km.items()):
        gallons = (km * MI_PER_KM) / ice_mpg
        station_price, average_price = _find_gas_price(prices, year, month)
        if station_price is not None:
            gas_total_station += gallons * station_price
            station_prices_seen.append(station_price)
        if average_price is not None:
            gas_total_average += gallons * average_price
            average_prices_seen.append(average_price)
        total_distance_km += km

    # EV side: all-in actual spend (every costed session in the window plus
    # prorated subscription fees) — not gated on any distance field.
    networks_by_name = await get_networks_by_name(db)
    subs_by_network = await get_all_subscriptions_by_network(db)

    stmt = select(EVChargingSession)
    time_filter = build_time_filter(time_range, date_from, date_to)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)
    sessions = (await db.execute(stmt)).scalars().all()

    ev_energy = 0.0
    session_count = 0
    range_start_min = None
    range_end_max = None
    for s in sessions:
        cost_info = compute_session_cost(s, networks_by_name)
        if cost_info["display_cost"] is None or s.session_start_utc is None:
            continue
        ev_energy += cost_info["display_cost"]
        session_count += 1
        session_date = s.session_start_utc.date()
        range_start_min = session_date if range_start_min is None else min(range_start_min, session_date)
        range_end_max = session_date if range_end_max is None else max(range_end_max, session_date)

    ev_fees = 0.0
    if range_start_min is not None and range_end_max is not None:
        for periods in subs_by_network.values():
            ev_fees += calculate_monthly_fees_in_range(periods, range_start_min, range_end_max)
    ev_total = ev_energy + ev_fees

    if station_prices_seen and average_prices_seen:
        gas_total_low = min(gas_total_station, gas_total_average)
        gas_total_high = max(gas_total_station, gas_total_average)
        has_range = gas_total_low != gas_total_high
    elif station_prices_seen:
        gas_total_low = gas_total_high = gas_total_station
        has_range = False
    else:
        gas_total_low = gas_total_high = gas_total_average
        has_range = False

    return {
        "has_data": True,
        "distance_source": distance_source,
        "total_distance": total_distance_km,  # km, metric base
        "ev_total": ev_total,
        "ev_energy": ev_energy,
        "ev_fees": ev_fees,
        "session_count": session_count,
        "gas_total_station": gas_total_station,
        "gas_total_average": gas_total_average,
        "gas_total_low": gas_total_low,
        "gas_total_high": gas_total_high,
        "savings_low": gas_total_low - ev_total,
        "savings_high": gas_total_high - ev_total,
        "has_range": has_range,
        "ice_label": ice_vehicle.label,
        "ice_l_per_100km": ice_l_per_100km,
        "station_price_min": min(station_prices_seen) if station_prices_seen else None,
        "station_price_max": max(station_prices_seen) if station_prices_seen else None,
        "average_price_min": min(average_prices_seen) if average_prices_seen else None,
        "average_price_max": max(average_prices_seen) if average_prices_seen else None,
    }


