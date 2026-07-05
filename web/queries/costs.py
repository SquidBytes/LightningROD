"""Charging cost calculations, summaries, and charts."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from db.models.charging_session import EVChargingSession
from db.models.reference import (
    EVChargingNetwork,
    EVLocationLookup,
    EVNetworkSubscription,
)
from web.queries.time_window import resolve_time_window, window_clause

# km -> miles factor; distance_added is stored in km.
_KM_TO_MI = 0.621371

# Shared Plotly modebar config — show minimal controls, hide logo
_PLOTLY_CONFIG = {
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "displaylogo": False,
}

_HOVER_LABEL = dict(bgcolor="#1f2937", font_color="#e5e7eb", bordercolor="#374151")


def _wrap_chart(html: str) -> str:
    """Wrap Plotly HTML in a container for modebar positioning."""
    return f'<div class="plotly-chart-wrap">{html}</div>'


def build_time_filter(
    range_str: str,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Return a SQLAlchemy where clause for EVChargingSession.session_start_utc.

    Accepts presets '7d', '30d', '90d', 'ytd', '1y', 'all' plus an optional
    custom yyyy-mm-dd window (applied when no preset is active).
    Returns None when unbounded.
    """
    start, end = resolve_time_window(range_str, date_from, date_to)
    return window_clause(EVChargingSession.session_start_utc, start, end)


def find_active_subscription(
    periods: list,
    session_date,
) -> EVNetworkSubscription | None:
    """Find the subscription period active on a given date, if any."""
    for period in periods:
        if period.start_date <= session_date:
            if period.end_date is None or session_date <= period.end_date:
                return period
    return None


def compute_session_cost(
    session,
    network=None,
    location=None,
    *,
    networks_by_name: dict | None = None,
    subscription_periods: list | None = None,
) -> dict:
    """Compute display cost for a session via the cost-hierarchy cascade.

    Cascade order: is_free flag, stored user cost, location cost_per_kwh,
    network cost_per_kwh, then no cost data. A network/location pair may be
    passed directly, or a positional `networks_by_name` dict for name lookup.
    """
    # A dict in the `network` slot is treated as networks_by_name.
    if isinstance(network, dict):
        networks_by_name = network
        network = None

    if network is None and networks_by_name is not None:
        if session.location_name and session.location_name in networks_by_name:
            network = networks_by_name[session.location_name]

    result: dict[str, Any] = {
        "display_cost": None,
        "cost_source": None,
        "is_free": False,
        "cost_per_kwh": None,
        "calculation": None,
        "estimated_cost": None,
        "actual_cost_per_kwh": None,
        "cost_difference": None,
        "subscription_active": False,
        "non_member_cost": None,
        "savings": None,
    }

    energy_kwh = float(session.energy_kwh or 0)

    # Resolve the subscription active on this session's date.
    active_sub = None
    session_date = None
    if subscription_periods and session.session_start_utc:
        session_date = session.session_start_utc.date()
        active_sub = find_active_subscription(subscription_periods, session_date)

    # Estimated cost from the hierarchy: location rate, then network rate.
    estimated_cost = None

    if location and location.cost_per_kwh:
        cost_val = float(location.cost_per_kwh)
        estimated_cost = energy_kwh * cost_val
    elif network and not network.is_free and network.cost_per_kwh:
        if active_sub:
            cost_val = float(active_sub.member_rate)
        else:
            cost_val = float(network.cost_per_kwh)
        estimated_cost = energy_kwh * cost_val

    if estimated_cost is not None:
        result["estimated_cost"] = round(estimated_cost, 4)

    if session.cost is not None and energy_kwh > 0:
        result["actual_cost_per_kwh"] = round(float(session.cost) / energy_kwh, 4)

    if session.cost is not None and estimated_cost is not None:
        result["cost_difference"] = round(float(session.cost) - estimated_cost, 4)

    # display_cost cascade.
    # (a) Session-level is_free flag
    if session.is_free:
        result["display_cost"] = 0.0
        result["cost_source"] = "calculated"
        result["is_free"] = True

    # (b) Stored cost (manual or imported) always takes priority for display
    elif session.cost is not None:
        result["display_cost"] = float(session.cost)
        result["cost_source"] = session.cost_source or "imported"

    # (c) Network-level is_free
    elif network and network.is_free:
        result["display_cost"] = 0.0
        result["cost_source"] = "calculated"
        result["is_free"] = True

    # (d) Location cost override
    elif location and location.cost_per_kwh:
        cost_val = float(location.cost_per_kwh)
        result["display_cost"] = round(energy_kwh * cost_val, 4)
        result["cost_source"] = "calculated"
        result["cost_per_kwh"] = cost_val
        result["calculation"] = f"{energy_kwh} kWh x ${cost_val}/kWh (location)"

    # (e) Network cost — use subscription member_rate if active, else network base rate
    elif network and network.cost_per_kwh:
        if active_sub:
            cost_val = float(active_sub.member_rate)
            result["display_cost"] = round(energy_kwh * cost_val, 4)
            result["cost_source"] = "calculated"
            result["cost_per_kwh"] = cost_val
            result["calculation"] = f"{energy_kwh} kWh x ${cost_val}/kWh (member)"
        else:
            cost_val = float(network.cost_per_kwh)
            result["display_cost"] = round(energy_kwh * cost_val, 4)
            result["cost_source"] = "calculated"
            result["cost_per_kwh"] = cost_val
            result["calculation"] = f"{energy_kwh} kWh x ${cost_val}/kWh"

    # (f) No cost data available — result stays with None display_cost

    # Subscription savings — non_member_cost is energy at the non-member rate.
    if active_sub and network and network.cost_per_kwh and energy_kwh > 0:
        result["subscription_active"] = True
        non_member = energy_kwh * float(network.cost_per_kwh)
        result["non_member_cost"] = round(non_member, 4)
        if result["display_cost"] is not None:
            result["savings"] = round(non_member - result["display_cost"], 4)

    return result


async def get_networks_by_name(db: AsyncSession) -> dict[str, EVChargingNetwork]:
    """Return dict of network_name -> EVChargingNetwork for all networks."""
    result = await db.execute(select(EVChargingNetwork))
    networks = result.scalars().all()
    return {network.network_name: network for network in networks}


async def get_networks_by_id(db: AsyncSession) -> dict[int, EVChargingNetwork]:
    """Return dict of network_id -> EVChargingNetwork for all networks."""
    result = await db.execute(select(EVChargingNetwork))
    networks = result.scalars().all()
    return {network.id: network for network in networks}


async def get_locations_by_id(
    db: AsyncSession, location_ids: list[int]
) -> dict[int, EVLocationLookup]:
    """Return dict of location_id -> EVLocationLookup for given IDs."""
    if not location_ids:
        return {}
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id.in_(location_ids))
    )
    locations = result.scalars().all()
    return {loc.id: loc for loc in locations}


async def get_session_cost_context(
    db: AsyncSession, session
) -> tuple[EVChargingNetwork | None, EVLocationLookup | None]:
    """Load the network and location objects for a session's cost calculation.

    Returns (network, location) tuple, either or both may be None.
    """
    network: EVChargingNetwork | None = None
    location: EVLocationLookup | None = None
    if session.network_id:
        net_result = await db.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == session.network_id)
        )
        network = net_result.scalar_one_or_none()
    if session.location_id:
        loc_result = await db.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == session.location_id)
        )
        location = loc_result.scalar_one_or_none()
    return network, location


async def query_cost_summary(db: AsyncSession, time_range: str = "all", device_id: str | None = None) -> dict:
    """Cost summary for the range, aggregated by network.

    Uses network_id FK lookup with the location cost cascade.
    """
    from web.queries.settings import get_all_subscriptions_by_network
    networks_by_id = await get_networks_by_id(db)
    subs_by_network = await get_all_subscriptions_by_network(db)

    stmt = select(EVChargingSession).options(
        load_only(
            EVChargingSession.id,
            EVChargingSession.energy_kwh,
            EVChargingSession.cost,
            EVChargingSession.cost_source,
            EVChargingSession.is_free,
            EVChargingSession.location_name,
            EVChargingSession.location_type,
            EVChargingSession.network_id,
            EVChargingSession.location_id,
            EVChargingSession.device_id,
            EVChargingSession.session_start_utc,
            EVChargingSession.estimated_cost,
        )
    )
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    location_ids = [s.location_id for s in sessions if s.location_id]
    locations_by_id = await get_locations_by_id(db, location_ids)

    total_cost = 0.0
    free_total_kwh = 0.0
    free_session_count = 0
    unconfigured_count = 0
    total_sessions = 0
    total_kwh = 0.0
    actual_total_cost = 0.0
    estimated_total_cost = 0.0
    actual_session_count = 0
    estimated_session_count = 0
    subscription_total_saved = 0.0
    subscription_by_network: dict[str, dict] = {}
    by_network: dict[str, dict] = {}

    for s in sessions:
        network = networks_by_id.get(s.network_id) if s.network_id else None
        location = locations_by_id.get(s.location_id) if s.location_id else None
        sub_periods = subs_by_network.get(s.network_id, []) if s.network_id else []
        cost_info = compute_session_cost(s, network=network, location=location, subscription_periods=sub_periods)

        if cost_info["display_cost"] is None:
            unconfigured_count += 1
            continue

        total_sessions += 1
        kwh = float(s.energy_kwh or 0)
        total_kwh += kwh
        total_cost += cost_info["display_cost"]

        if cost_info["is_free"]:
            free_total_kwh += kwh
            free_session_count += 1

        if s.cost is not None and s.cost_source in ("manual", "imported"):
            actual_total_cost += float(s.cost)
            actual_session_count += 1
        elif s.estimated_cost is not None:
            estimated_total_cost += float(s.estimated_cost)
            estimated_session_count += 1
        elif cost_info["display_cost"] is not None:
            estimated_total_cost += cost_info["display_cost"]
            estimated_session_count += 1

        net_name = network.network_name if network else (s.location_name or s.location_type or "Unknown")
        if net_name not in by_network:
            by_network[net_name] = {
                "network": net_name,
                "total_cost": 0.0,
                "session_count": 0,
                "total_kwh": 0.0,
            }
        by_network[net_name]["total_cost"] += cost_info["display_cost"]
        by_network[net_name]["session_count"] += 1
        by_network[net_name]["total_kwh"] += kwh

        if cost_info["subscription_active"] and cost_info["savings"] is not None:
            subscription_total_saved += cost_info["savings"]
            if net_name not in subscription_by_network:
                subscription_by_network[net_name] = {
                    "total_saved": 0.0,
                    "session_count": 0,
                    "member_sessions": 0,
                }
            subscription_by_network[net_name]["total_saved"] += cost_info["savings"]
            subscription_by_network[net_name]["session_count"] += 1
            subscription_by_network[net_name]["member_sessions"] += 1

    return {
        "total_cost": total_cost,
        "free_total_kwh": free_total_kwh,
        "free_session_count": free_session_count,
        "by_network": list(by_network.values()),
        "unconfigured_count": unconfigured_count,
        "total_sessions": total_sessions,
        "total_kwh": total_kwh,
        "actual_total_cost": actual_total_cost,
        "estimated_total_cost": estimated_total_cost,
        "actual_session_count": actual_session_count,
        "estimated_session_count": estimated_session_count,
        "subscription_total_saved": subscription_total_saved,
        "subscription_by_network": subscription_by_network,
    }


def calculate_monthly_fees_in_range(
    periods: list,
    range_start,
    range_end,
) -> float:
    """Sum monthly fees for subscription periods that overlap the given date range.

    Each calendar month with any overlap is counted as a full month,
    matching how subscription services typically bill.
    """
    total_fees = 0.0
    for period in periods:
        p_start = max(period.start_date, range_start)
        p_end = min(period.end_date or range_end, range_end)
        if p_start > p_end:
            continue
        # Count each calendar month touched as a full month
        months = ((p_end.year - p_start.year) * 12 + p_end.month - p_start.month) + 1
        total_fees += float(period.monthly_fee) * months
    return total_fees


# Summary-row ratio helpers. Each returns None when the denominator is zero or
# no usable rows exist — the template renders `—` rather than `$0.00`/NaN.


async def avg_cost_per_session(
    db: AsyncSession,
    device_id: str | None = None,
    time_range: str = "all",
) -> float | None:
    """Mean `total_cost` over sessions that have a recorded cost in the range.

    Returns None when there are no cost-bearing sessions.
    """
    stmt = select(
        func.sum(EVChargingSession.cost).label("num"),
        func.count(EVChargingSession.id).label("n"),
    ).where(EVChargingSession.cost.isnot(None))

    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    row = (await db.execute(stmt)).one()
    if row.n is None or int(row.n) == 0 or row.num is None:
        return None
    return float(row.num) / float(row.n)


async def cost_per_mile(
    db: AsyncSession,
    device_id: str | None = None,
    time_range: str = "all",
) -> float | None:
    """Sum(cost) / Sum(distance_added_km * 0.621371) over the range.

    Sessions with `distance_added IS NULL` are excluded from both the
    numerator and denominator. Returns None when denominator is 0.
    """
    stmt = (
        select(
            func.sum(EVChargingSession.cost).label("num"),
            func.sum(EVChargingSession.distance_added * _KM_TO_MI).label("denom"),
        )
        .where(EVChargingSession.cost.isnot(None))
        .where(EVChargingSession.distance_added.isnot(None))
        .where(EVChargingSession.distance_added > 0)
    )

    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    row = (await db.execute(stmt)).one()
    if row.denom is None or float(row.denom) == 0.0 or row.num is None:
        return None
    return float(row.num) / float(row.denom)


async def cost_per_kwh(
    db: AsyncSession,
    device_id: str | None = None,
    time_range: str = "all",
) -> float | None:
    """Sum(cost) / Sum(energy_kwh) over the range.

    Rows where either side is NULL or energy is zero are excluded from both
    the numerator and denominator. Returns None when denominator is 0.
    """
    stmt = (
        select(
            func.sum(EVChargingSession.cost).label("num"),
            func.sum(EVChargingSession.energy_kwh).label("denom"),
        )
        .where(EVChargingSession.cost.isnot(None))
        .where(EVChargingSession.energy_kwh.isnot(None))
        .where(EVChargingSession.energy_kwh > 0)
    )

    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    row = (await db.execute(stmt)).one()
    if row.denom is None or float(row.denom) == 0.0 or row.num is None:
        return None
    return float(row.num) / float(row.denom)


async def free_charging_savings(
    db: AsyncSession,
    device_id: str | None = None,
    time_range: str = "all",
) -> float:
    """Total dollars saved via `is_free=True` sessions in the range.

    Computed as Sum(estimated_cost) over is_free sessions — estimated_cost is
    the pre-cascade "would-have-cost" value populated by compute_session_cost
    (see query_cost_summary). Returns 0.0 when no free sessions exist.
    """
    stmt = (
        select(func.sum(EVChargingSession.estimated_cost))
        .where(EVChargingSession.is_free.is_(True))
        .where(EVChargingSession.estimated_cost.isnot(None))
    )

    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    row = (await db.execute(stmt)).one()
    total = row[0]
    return float(total) if total is not None else 0.0


