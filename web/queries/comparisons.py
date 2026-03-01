from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.queries.costs import build_time_filter, compute_session_cost, get_networks_by_name
from web.queries.settings import get_app_settings_dict


async def query_gas_comparison(db: AsyncSession, time_range: str = "all") -> dict:
    """Compare actual EV charging cost to equivalent gasoline cost.

    Only includes sessions where:
    - miles_added > 0
    - display_cost is not None (network is configured)

    Returns dict with:
    - ev_total: float — sum of actual EV charging costs
    - gas_total: float — sum of equivalent gas costs
    - savings: float — gas_total - ev_total (positive = EV cheaper)
    - savings_pct: float — savings as percentage of gas_total
    - session_count: int
    - total_miles: float
    - gas_price: float — $/gallon used for calculation
    - mpg: float — vehicle MPG used for calculation
    """
    settings = await get_app_settings_dict(db, ["gas_price_per_gallon", "vehicle_mpg"])
    gas_price = float(settings.get("gas_price_per_gallon") or "3.50")
    mpg = float(settings.get("vehicle_mpg") or "28.0")

    networks_by_name = await get_networks_by_name(db)

    stmt = select(EVChargingSession).where(EVChargingSession.miles_added > 0)
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    ev_total = 0.0
    gas_total = 0.0
    session_count = 0
    total_miles = 0.0

    for s in sessions:
        cost_info = compute_session_cost(s, networks_by_name)
        if cost_info["display_cost"] is None:
            continue

        miles = float(s.miles_added)
        gas_cost = (miles / mpg) * gas_price

        ev_total += cost_info["display_cost"]
        gas_total += gas_cost
        session_count += 1
        total_miles += miles

    savings = gas_total - ev_total
    savings_pct = (savings / gas_total * 100) if gas_total > 0 else 0.0

    return {
        "ev_total": ev_total,
        "gas_total": gas_total,
        "savings": savings,
        "savings_pct": savings_pct,
        "session_count": session_count,
        "total_miles": total_miles,
        "gas_price": gas_price,
        "mpg": mpg,
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
