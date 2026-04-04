from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import GasPriceHistory
from db.models.vehicle import EVVehicle
from web.queries.costs import build_time_filter, compute_session_cost, get_networks_by_name
from web.unit_system import GAL_PER_LITER, MI_PER_KM


def _find_gas_price(
    prices: list[GasPriceHistory], year: int, month: int
) -> tuple[Optional[float], Optional[float]]:
    """Find the gas price entry for (year, month) or nearest earlier month.

    Prices must be sorted by (year DESC, month DESC).
    Returns (station_price, average_price). Defaults to (3.50, 3.50) if no entries.
    """
    for entry in prices:
        if (entry.year, entry.month) <= (year, month):
            station = float(entry.station_price) if entry.station_price is not None else None
            average = float(entry.average_price) if entry.average_price is not None else None
            return (station, average)
    # No entry found — use default
    return (3.50, 3.50)


def _empty_gas_result() -> dict:
    """Return a zeroed-out gas comparison result dict."""
    return {
        "ev_total": 0.0,
        "gas_total_low": 0.0,
        "gas_total_high": 0.0,
        "savings_low": 0.0,
        "savings_high": 0.0,
        "savings_pct_low": 0.0,
        "savings_pct_high": 0.0,
        "session_count": 0,
        "total_distance": 0.0,  # km (metric base)
        "ice_label": None,
        "has_range": False,
    }


async def query_gas_comparison(
    db: AsyncSession,
    device_id: Optional[str] = None,
    vehicle: Optional[EVVehicle] = None,
    time_range: str = "all",
) -> dict:
    """Compare actual EV charging cost to equivalent gasoline cost.

    Uses date-aware gas price lookup with two price tracks (station and average)
    to produce a savings range. Supports dual calculation paths:
    - Primary (distance-based): when session.distance_added > 0 and
      vehicle.ice_fuel_efficiency set
    - Fallback (percentage-based): when session.energy_kwh > 0 and vehicle has
      battery_capacity_kwh and ice_fuel_tank_capacity

    All stored values are metric (km, L/100km, liters). Gas prices from external
    US sources are in $/gallon, so we convert distance to miles and efficiency
    to MPG internally for the cost math.

    Returns dict with:
    - ev_total, gas_total_low, gas_total_high
    - savings_low, savings_high, savings_pct_low, savings_pct_high
    - session_count, total_distance (km), ice_label, has_range
    """
    # If no vehicle or no ICE config, return empty result
    if vehicle is None or not vehicle.ice_fuel_efficiency:
        return _empty_gas_result()

    # L/100km stored in DB -> convert to MPG for gas math
    ice_l_per_100km = float(vehicle.ice_fuel_efficiency)
    ice_mpg = 235.215 / ice_l_per_100km if ice_l_per_100km > 0 else None
    battery_kwh = float(vehicle.battery_capacity_kwh) if vehicle.battery_capacity_kwh else None
    # Tank capacity stored in liters -> convert to gallons for gas math
    fuel_tank_liters = float(vehicle.ice_fuel_tank_capacity) if vehicle.ice_fuel_tank_capacity else None
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
        if average_price is not None:
            gas_total_average += gallons * average_price
            average_has_data = True

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
        "savings_low": savings_low,
        "savings_high": savings_high,
        "savings_pct_low": savings_pct_low,
        "savings_pct_high": savings_pct_high,
        "session_count": session_count,
        "total_distance": total_distance_km,  # km, metric base
        "ice_label": vehicle.ice_label,
        "has_range": has_range,
    }


async def query_network_comparison(
    db: AsyncSession, reference_rate: float, time_range: str = "all"
) -> dict:
    """Compare actual EV charging cost to hypothetical cost at a reference rate.

    Only includes sessions where:
    - energy_kwh > 0
    - display_cost is not None (network is configured)

    Returns dict with:
    - ev_total: float — sum of actual EV charging costs
    - hypothetical_total: float — sum of costs at reference rate
    - difference: float — hypothetical_total - ev_total (positive = EV cheaper)
    - difference_pct: float — difference as percentage of hypothetical_total
    - session_count: int
    - total_kwh: float
    - reference_rate: float — rate used for hypothetical calculation
    """
    networks_by_name = await get_networks_by_name(db)

    stmt = select(EVChargingSession).where(EVChargingSession.energy_kwh > 0)
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    ev_total = 0.0
    hypothetical_total = 0.0
    session_count = 0
    total_kwh = 0.0

    for s in sessions:
        cost_info = compute_session_cost(s, networks_by_name)
        if cost_info["display_cost"] is None:
            continue

        kwh = float(s.energy_kwh)
        hypothetical_cost = kwh * reference_rate

        ev_total += cost_info["display_cost"]
        hypothetical_total += hypothetical_cost
        session_count += 1
        total_kwh += kwh

    difference = hypothetical_total - ev_total
    difference_pct = (difference / hypothetical_total * 100) if hypothetical_total > 0 else 0.0

    return {
        "ev_total": ev_total,
        "hypothetical_total": hypothetical_total,
        "difference": difference,
        "difference_pct": difference_pct,
        "session_count": session_count,
        "total_kwh": total_kwh,
        "reference_rate": reference_rate,
    }
