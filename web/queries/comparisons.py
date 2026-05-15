"""EV-to-ICE comparison calculations and chart helpers."""


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.ice_vehicle import IceVehicle
from db.models.reference import GasPriceHistory
from db.models.vehicle import EVVehicle
from web.queries.costs import (
    build_time_filter,
    compute_session_cost,
    get_networks_by_name,
)
from web.unit_system import GAL_PER_LITER, LITER_PER_GAL, MI_PER_KM


def _find_gas_price(
    prices: list[GasPriceHistory], year: int, month: int
) -> tuple[float | None, float | None]:
    """Find the gas price entry for (year, month) or nearest earlier month.

    Prices must be sorted by (year DESC, month DESC).
    Returns (station_price, average_price) in $/gal. Defaults to (3.50, 3.50)
    if no entries. Storage is $/L (post-Phase-33 migration); the multiply by
    LITER_PER_GAL converts at the read boundary so the gallons-based cost
    math at the call site stays unchanged.
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
) -> dict:
    """Compare actual EV charging cost to equivalent gasoline cost.

    Uses date-aware gas price lookup with two price tracks (station and average)
    to produce a savings range. Supports dual calculation paths:
    - Primary (distance-based): when session.distance_added > 0 and
      ice_vehicle.fuel_efficiency_l_per_100km set
    - Fallback (percentage-based): when session.energy_kwh > 0 and vehicle has
      battery_capacity_kwh and ice_vehicle.tank_capacity_l

    All stored values are metric (km, L/100km, liters). Gas prices from external
    US sources are in $/gallon, so we convert distance to miles and efficiency
    to MPG internally for the cost math.

    Returns dict with:
    - ev_total, gas_total_low, gas_total_high
    - savings_low, savings_high, savings_pct_low, savings_pct_high
    - session_count, total_distance (km), ice_label, has_range
    """
    # If no ICE vehicle configured, return empty result
    if ice_vehicle is None or not ice_vehicle.fuel_efficiency_l_per_100km:
        return _empty_gas_result()

    # L/100km stored in DB -> convert to MPG for gas math
    ice_l_per_100km = float(ice_vehicle.fuel_efficiency_l_per_100km)
    ice_mpg = 235.215 / ice_l_per_100km if ice_l_per_100km > 0 else None
    # The fallback gas-equivalent path below divides `session.energy_kwh` by
    # pack capacity to get a "percent of tank" figure. That only makes sense
    # against USABLE capacity — energy_kwh is what the charger actually put
    # into the pack, not the gross cell headroom. Battery is an EV concept;
    # source remains the EVVehicle row.
    battery_kwh = float(vehicle.battery_capacity_kwh) if vehicle and vehicle.battery_capacity_kwh else None
    # Tank capacity stored in liters -> convert to gallons for gas math
    fuel_tank_liters = float(ice_vehicle.tank_capacity_l) if ice_vehicle.tank_capacity_l else None
    fuel_tank_gal = fuel_tank_liters * GAL_PER_LITER if fuel_tank_liters else None

    # Load all gas price history into memory (small table)
    price_result = await db.execute(
        select(GasPriceHistory).order_by(
            GasPriceHistory.year.desc(), GasPriceHistory.month.desc()
        )
    )
    prices = list(price_result.scalars().all())

    networks_by_name = await get_networks_by_name(db)

    # Build session query
    stmt = select(EVChargingSession)
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    ev_total = 0.0
    gas_total_station = 0.0
    gas_total_average = 0.0
    station_has_data = False
    average_has_data = False
    session_count = 0
    total_distance_km = 0.0
    station_prices_seen: list[float] = []
    average_prices_seen: list[float] = []

    for s in sessions:
        cost_info = compute_session_cost(s, networks_by_name)
        if cost_info["display_cost"] is None:
            continue

        # Determine gallons equivalent via dual calculation path
        gallons = None
        distance_km = float(s.distance_added) if s.distance_added else 0.0
        distance_mi = distance_km * MI_PER_KM

        if distance_mi > 0 and ice_mpg:
            # Primary: distance-based (convert km->mi for MPG math)
            gallons = distance_mi / ice_mpg
        elif (
            s.energy_kwh
            and float(s.energy_kwh) > 0
            and battery_kwh
            and fuel_tank_gal
        ):
            # Fallback: percentage-based
            pct = float(s.energy_kwh) / battery_kwh
            gallons = pct * fuel_tank_gal

        if gallons is None:
            continue

        # Look up gas price for session's month
        if s.session_start_utc is None:
            continue
        s_year = s.session_start_utc.year
        s_month = s.session_start_utc.month
        station_price, average_price = _find_gas_price(prices, s_year, s_month)

        # Accumulate costs per track
        if station_price is not None:
            gas_total_station += gallons * station_price
            station_has_data = True
            station_prices_seen.append(station_price)
        if average_price is not None:
            gas_total_average += gallons * average_price
            average_has_data = True
            average_prices_seen.append(average_price)

        ev_total += cost_info["display_cost"]
        session_count += 1
        total_distance_km += distance_km

    # Determine low/high bounds from the two tracks
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

    savings_low = gas_total_low - ev_total
    savings_high = gas_total_high - ev_total
    savings_pct_low = (savings_low / gas_total_low * 100) if gas_total_low > 0 else 0.0
    savings_pct_high = (savings_high / gas_total_high * 100) if gas_total_high > 0 else 0.0

    return {
        "ev_total": ev_total,
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


